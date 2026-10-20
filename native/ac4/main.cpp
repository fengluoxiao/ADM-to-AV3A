// GPL-3.0-or-later. Thin streaming adapter over an unmodified ICL Forge library.
#include <algorithm>
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <span>
#include <stdexcept>
#include <vector>
#include <nlohmann/json.hpp>
#include "iclforge/ac4/encoder/encoder.hpp"
#include "iclforge/ac4/io/carriage.hpp"

using nlohmann::json;
namespace ac4 = iclforge::ac4;

ac4::ObjectProperties properties(const json& j) {
    ac4::ObjectProperties p;
    p.position = j.at("position").get<std::array<double, 3>>();
    p.width = j.at("width").get<std::array<double, 3>>();
    // Source gain is already applied sample by sample to the float PCM.
    p.gain_db = 0;
    return p;
}

int main(int argc, char** argv) {
    try {
        if (argc != 6) throw std::runtime_error("usage: Ac4Encode config.json input.f32 scene.jsonl output.ac4 result.json");
        std::ifstream cfg_file(argv[1]);
        const json cfg = json::parse(cfg_file);
        const int channels = cfg.at("channels").get<int>();
        const auto samples = cfg.at("samples").get<std::int64_t>();
        if (channels < 1 || channels > 64 || samples <= 0) throw std::runtime_error("invalid channel count or length");
        ac4::EncoderConfig config;
        config.sample_rate_hz = 48000;
        config.frame_rate_index = 13;
        config.bitrate_kbps = cfg.at("bitrate_kbps").get<int>();
        config.experimental.objects = true;
        ac4::ObjectsConfig objects;
        objects.coding = ac4::ObjectCoding::kAjoc;
        objects.downmix = ac4::AjocDownmix::kComputed;
        for (const auto& j : cfg.at("objects")) {
            ac4::ObjectConfig o;
            o.properties = properties(j);
            o.lfe = j.value("lfe", false);
            if (j.contains("bed")) o.bed = static_cast<ac4::BedChannel>(j.at("bed").get<int>());
            objects.objects.push_back(o);
        }
        if (objects.objects.size() != static_cast<std::size_t>(channels)) throw std::runtime_error("scene channel mismatch");
        ac4::SubstreamConfig sub;
        // Use ASPX consistently, including small scenes at high bitrates.
        sub.codec_mode = ac4::CodecMode::kAspx;
        sub.objects = objects;
        config.substreams = {sub};
        auto encoder = ac4::Encoder::create(config);
        if (!encoder) throw std::runtime_error(std::string(ac4::Encoder::refusal_reason(config)));
        const auto dac4 = ac4::build_dac4(encoder->toc());
        if (dac4.empty()) throw std::runtime_error(std::string(ac4::dac4_refusal(encoder->toc())));
        json result = {{"frame_samples", 2048}, {"encoder_delay_samples", encoder->delay_samples()},
                       {"decoder_delay_samples", encoder->decoder_delay_samples()},
                       {"codec_string", ac4::rfc6381_codec_string(encoder->toc())},
                       {"b_static_dmx", false}, {"frames", 0}};
        result["sync_samples"] = json::array();
        result["dac4"] = json::array();
        for (std::byte b : dac4) result["dac4"].push_back(std::to_integer<unsigned>(b));
        std::ifstream pcm(argv[2], std::ios::binary), metadata(argv[3]);
        std::ofstream output(argv[4], std::ios::binary);
        if (!pcm || !metadata || !output) throw std::runtime_error("cannot open adapter input/output");
        json pending;
        std::string line;
        auto next = [&]() { pending = std::getline(metadata, line) ? json::parse(line) : json(); };
        next();
        std::int64_t frames = 0;
        auto write = [&](const std::vector<ac4::EncodedFrame>& encoded) {
            for (const auto& frame : encoded) {
                if (frame.iframe) result["sync_samples"].push_back(frames + 1);
                const auto bytes = ac4::sync_frame(frame.raw_ac4_frame, true);
                output.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
                if (!output) throw std::runtime_error("cannot write AC-4 frame");
                ++frames;
            }
            output.flush();
        };
        for (std::int64_t start = 0; start < samples; start += 2048) {
            const auto count = static_cast<std::size_t>(std::min<std::int64_t>(2048, samples - start));
            std::vector<float> interleaved(count * static_cast<std::size_t>(channels));
            const auto bytes = static_cast<std::streamsize>(interleaved.size() * sizeof(float));
            pcm.read(reinterpret_cast<char*>(interleaved.data()), bytes);
            if (pcm.gcount() != bytes) throw std::runtime_error("truncated PCM");
            std::vector<std::vector<float>> planar(static_cast<std::size_t>(channels), std::vector<float>(count));
            std::vector<std::span<const float>> views;
            for (int ch = 0; ch < channels; ++ch) {
                for (std::size_t i = 0; i < count; ++i) planar[ch][i] = interleaved[i * channels + ch];
                views.emplace_back(planar[ch]);
            }
            std::vector<ac4::ObjectMetadataUpdate> updates;
            while (!pending.is_null() && pending.at("sample").get<std::int64_t>() < start + static_cast<std::int64_t>(count)) {
                ac4::ObjectMetadataUpdate u;
                u.object = pending.at("object").get<int>();
                u.sample = pending.at("sample").get<std::int64_t>();
                u.ramp_samples = pending.at("ramp").get<int>();
                u.properties = properties(pending);
                updates.push_back(u);
                next();
            }
            auto encoded = encoder->encode(views, updates);
            if (!encoded) throw std::runtime_error(std::string(ac4::describe(encoded.error())));
            write(*encoded);
            if (start % (2048 * 24) == 0) std::cout << json({{"samples", start + count}, {"total", samples}}).dump() << std::endl;
        }
        auto tail = encoder->flush();
        if (!tail) throw std::runtime_error(std::string(ac4::describe(tail.error())));
        write(*tail);
        output.close();
        result["frames"] = frames;
        std::ofstream receipt(argv[5]);
        receipt << result.dump(2) << '\n';
        if (!receipt) throw std::runtime_error("cannot write result.json");
        return 0;
    } catch (const std::exception& e) {
        std::cerr << e.what() << std::endl;
        return 1;
    }
}
