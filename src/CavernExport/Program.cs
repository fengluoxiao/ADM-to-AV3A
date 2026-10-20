// Original integration code. Cavern is used under licenses/Cavern.txt.
using System.Globalization;
using System.Text;
using System.Text.Json;
using Cavern;
using Cavern.Format;
using Cavern.Format.Environment;
using Cavern.Format.Renderers;
using Cavern.SpecialSources;

internal static class Program
{
    const string Commit = "583fe204df95c83ec3ecb78bba63986e437a911d";
    static void Emit(object value) => Console.WriteLine(JsonSerializer.Serialize(value));

    static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        try
        {
            if (args.Length < 2 || args[0] is not ("inspect" or "export"))
                throw new ArgumentException("Usage: CavernExport inspect|export INPUT [OUTPUT.wav SKIP_SAMPLES SAMPLE_COUNT]");
            using var reader = AudioReader.Open(args[1]);
            reader.ReadHeader();
            var renderer = reader.GetRenderer() as EnhancedAC3Renderer;
            // A channel-based renderer has no JOC applier. Do not dispose it through
            // EnhancedAC3Renderer.Dispose(), which assumes the applier is present.
            if (renderer == null || !renderer.HasObjects)
                throw new InvalidDataException("输入不是可解码的 E-AC-3 JOC Atmos 音轨；不回退到普通声床。");
            using (renderer)
            {
                if (reader.SampleRate != 48000 || reader.Length <= 0)
                    throw new InvalidDataException("当前导出要求 48 kHz 且具有明确长度的 JOC 音轨。");
                long total = reader.Length;
                long skip = args.Length > 3 ? long.Parse(args[3], CultureInfo.InvariantCulture) : 0;
                long length = args.Length > 4 ? long.Parse(args[4], CultureInfo.InvariantCulture) : total - skip;
                if (skip < 0 || length <= 0 || skip > total || length > total - skip)
                    throw new ArgumentException("导出采样区间超出输入音轨。");
                // Warm up sequentially: JOC filter and object history must survive
                // the requested cut. Reading the first block also initializes LFE.
                long consumed = 0;
                if (args[0] == "inspect")
                {
                    renderer.GetNextObjectSamples(256);
                    Emit(Info(reader, renderer, total));
                    return 0;
                }
                if (args.Length < 3) throw new ArgumentException("缺少输出 WAV 路径。");
                // Keep the upstream renderer's request size fixed. Its timeslot
                // buffers cannot be resized midway through a 32-sample slot.
                // This cache permits arbitrary MP4 edits and sample-accurate cuts
                // without patching Cavern or resetting its filter history.
                float[][]? cached = null;
                int cachePosition = 256;
                var sources = new List<Source>();
                float[][] Fetch(int samples)
                {
                    var result = new float[renderer.Objects.Count][];
                    for (int ch = 0; ch < result.Length; ch++) result[ch] = new float[samples];
                    int done = 0;
                    while (done < samples)
                    {
                        if (cachePosition == 256)
                        {
                            cached = renderer.GetNextObjectSamples(256);
                            cachePosition = 0;
                            if (!renderer.HasObjects)
                                throw new InvalidDataException("Cavern 在解码过程中丢失 JOC/OAMD；导出已停止。");
                        }
                        int n = Math.Min(samples - done, 256 - cachePosition);
                        for (int ch = 0; ch < result.Length; ch++)
                            Array.Copy(cached![ch], cachePosition, result[ch], done, n);
                        done += n;
                        cachePosition += n;
                    }
                    for (int ch = 0; ch < sources.Count; ch++)
                    {
                        var original = renderer.Objects[ch];
                        sources[ch].LFE = original.LFE;
                        sources[ch].Volume = original.Volume;
                        sources[ch].Position = original.Position;
                        sources[ch].Size = original.Size;
                    }
                    return result;
                }
                var master = new StreamMaster(Fetch);
                for (int i = 0; i < renderer.Objects.Count; i++)
                    sources.Add(new StreamMasterSource(master, i) { VolumeRolloff = Rolloffs.Disabled });
                master.SetupSources(sources, reader.SampleRate);
                while (consumed < skip)
                {
                    int n = (int)Math.Min(256, skip - consumed);
                    Fetch(n);
                    consumed += n;
                    if (consumed % 48000 < 256)
                        Emit(new { type = "progress", phase = "decode", progress = 30.0 * consumed / (skip + length), message = "Cavern：预热解码至所选区间" });
                }
                var listener = new Listener { SampleRate = reader.SampleRate, UpdateRate = 256 };
                listener.AttachSources(sources);
                // Python owns an isolated staging directory. Never open user output
                // paths directly here, so cancellation cannot publish a partial ADM.
                using (var timeline = new StreamWriter(Path.ChangeExtension(args[2], ".scene.jsonl"), false, new UTF8Encoding(false)))
                using (var writer = new BroadcastWaveFormatWriter(args[2], listener, length, BitDepth.Float32))
                {
                    for (long sample = 0; sample < length; sample += 256)
                    {
                        writer.WriteNextFrame();
                        if (!renderer.HasObjects)
                            throw new InvalidDataException("Cavern 在解码过程中丢失 JOC/OAMD；导出已停止。");
                        var objects = listener.ActiveSources.Select((source, index) => new {
                            index, name = source.LFE ? "LFE" : $"Cavern object {index + 1}", lfe = source.LFE,
                            gain = source.Volume,
                            x = source.Position.X / Listener.EnvironmentSize.X,
                            y = source.Position.Z / Listener.EnvironmentSize.Z,
                            z = source.Position.Y / Listener.EnvironmentSize.Y,
                            size = source.Size
                        }).ToArray();
                        timeline.WriteLine(JsonSerializer.Serialize(new { sample, objects }));
                        if (sample % 48000 < 256)
                            Emit(new { type = "progress", phase = "decode", progress = 30.0 * (skip + sample) / (skip + length), message = $"Cavern：导出对象 {sample / 48000.0:F1} / {length / 48000.0:F1} 秒" });
                    }
                }
                Emit(new { type = "exported", source = Info(reader, renderer, total), samples = length, skip_samples = skip,
                    metadata_sampling_samples = 256, pcm_gain_baked = false,
                    metadata_authority = "scene.jsonl includes source gain, size and LFE; embedded ADM is Cavern's position export" });
                return 0;
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex);
            Emit(new { type = "error", message = ex.Message });
            return 1;
        }
    }

    static object Info(AudioReader reader, EnhancedAC3Renderer renderer, long samples)
    {
        int lfe = -1;
        for (int i = 0; i < renderer.Objects.Count; i++) if (renderer.Objects[i].LFE) lfe = i;
        int channels = renderer.Objects.Count;
        return new { type = "cavern_inspection", decoder = "VoidXH/Cavern", decoder_commit = Commit,
            input_codec = "E-AC-3 JOC", channels, objects = channels - (lfe >= 0 ? 1 : 0),
            dynamic_objects = renderer.DynamicObjects, lfe = lfe >= 0 ? (int?)lfe : null,
            bed_channels = lfe >= 0 ? 1 : 0, bed_layout = lfe >= 0 ? "LFE" : null,
            fits_encoder = channels <= 16, encoder_capacity = 16, rate = reader.SampleRate,
            samples, seconds = samples / (double)reader.SampleRate, metadata_updates = (samples + 255) / 256,
            bitrate = channels * 96000, sparse_workaround = renderer.WorkedAround };
    }
}
