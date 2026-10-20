/* Read-only adapter around the pinned, unmodified Ittiam decoder library.
 * Calls its public API; snapshots the decoded OAM state because the upstream
 * external OAM writer omits values on some intra frames (has_obj_md is unset).
 * No decoder algorithms, PCM or metadata values are patched.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "decoder_headers.h"
#include "impeghd_mp4_parser.h"
#include "impeghd_mp4_file_wrapper.h"

static void *allocate(UWORD32 size, UWORD32 alignment) { return malloc(size + alignment); }
static void release(void *p) { free(p); }

static int refill(ia_file_wrapper *file, unsigned char *input, int capacity, int *available, int consumed) {
    UWORD32 got = 0;
    if (consumed < 0 || consumed > *available) return -1;
    *available -= consumed;
    memmove(input, input + consumed, *available);
    if (*available < capacity)
        impeghd_mp4_fw_read(file, input + *available, capacity - *available, &got);
    *available += got;
    return (int)got;
}

static int snapshot(FILE *meta, ia_mpegh_dec_api_struct *decoder, long long sample, int count) {
    ia_dec_data_struct *data = decoder->p_state_mpeghd->pstr_dec_data;
    ia_usac_config_struct *cfg = &data->str_frame_data.str_audio_specific_config.str_usac_config;
    ia_oam_dec_state_struct *oam = &data->str_obj_ren_dec_state.str_obj_md_dec_state;
    ia_enh_obj_md_frame_str *enh = &data->str_enh_obj_md_frame;
    int period = cfg->obj_md_cfg.frame_length;
    int selected = 0, subframes;
    if (oam->num_objects != count || period < 256 || period > 1024 || 1024 % period) return -1;
    subframes = 1024 / period;
    for (int sub = 0; sub < 1024 / period; ++sub) {
        /* The decoder packs only present subframes into the state arrays.
         * Follow its renderer's selection, including held prior-frame state. */
        if (subframes == 1) selected = 1;
        else if (oam->sub_frame_obj_md_present[sub]) {
            ++selected;
            if (selected > subframes) selected %= subframes;
        } else if (sub == 0) selected = subframes;
        fprintf(meta, "{\"sample\":%lld,\"period\":%d,\"objects\":[", sample + sub * period, period);
        for (int obj = 0; obj < count; ++obj) {
            int i = (selected - 1) * count + obj;
            fprintf(meta, "%s[%d,%d,%d,%d,%d,%d,%d,%d,%d,%.9g,%.9g,%d]", obj ? "," : "",
                obj, oam->azimuth[i], oam->elevation[i], oam->radius[i], oam->gain[i],
                oam->spread_width[i], cfg->obj_md_cfg.uniform_spread_present ? 0 : oam->spread_height[i],
                cfg->obj_md_cfg.uniform_spread_present ? 0 : oam->spread_depth[i], oam->dyn_obj_priority[i],
                (double)enh->diffuseness[obj], (double)enh->divergence[obj], enh->num_exclusion_sectors[obj]);
        }
        fputs("]}\n", meta);
    }
    return ferror(meta) ? -1 : 0;
}

int main(int argc, char **argv) {
    ia_mpeghd_api_struct api = {0};
    ia_input_config *in = &api.input_config;
    ia_output_config *out = &api.output_config;
    ia_file_wrapper *file = NULL;
    FILE *pcm = NULL, *meta = NULL, *original = NULL;
    int result = 1, created = 0, available = 0, capacity = 0, got, frames = 0, objects = 0;
    IA_ERRORCODE error = 0;
    unsigned char *input;
    ia_mpegh_dec_api_struct *decoder;
    if (argc != 2) { fprintf(stderr, "Usage: MpeghExport input.mp4|input.mhas\n"); return 1; }
    in->ui_mhas_flag = 1;
    in->ui_pcm_wd_sz = 24;
    in->ui_cicp_layout_idx = 2;
    in->i_preset_id = -1;
    in->extrn_rend_flag = 1;
    in->ptr_ext_ren_ch_data_buf = calloc(768, 1);
    in->ptr_ext_ren_oam_data_buf = calloc(768, 1);
    in->ptr_ext_ren_hoa_data_buf = calloc(768, 1);
    in->ptr_ext_ren_pcm_buf = calloc(1024 * 32, 4);
    if (!in->ptr_ext_ren_ch_data_buf || !in->ptr_ext_ren_oam_data_buf ||
        !in->ptr_ext_ren_hoa_data_buf || !in->ptr_ext_ren_pcm_buf) goto cleanup;
    out->malloc_mpegh = allocate;
    out->free_mpegh = release;
    file = impeghd_mp4_fw_open((WORD8 *)argv[1]);
    if (!file) { fputs("Cannot open input container.\n", stderr); goto cleanup; }
    if (file->is_mp4_file) impeghd_mp4_parse_mae_boxes(file, &api);
    in->ui_raw_flag = file->is_mp4_file && !file->is_mp4_mhm1;
    error = ia_mpegh_dec_create(in, out);
    if (error) goto cleanup;
    created = 1;
    decoder = out->pv_ia_process_api_obj;
    input = out->mem_info_table[IA_MEMTYPE_INPUT].mem_ptr;
    capacity = out->ui_inp_buf_size;
    do {
        got = refill(file, input, capacity, &available, out->i_bytes_consumed);
        if (got < 0 || available <= 0) { fputs("Incomplete MPEG-H configuration.\n", stderr); goto cleanup; }
        in->num_inp_bytes = available;
        error = ia_mpegh_dec_init(decoder, in, out);
        if (error && error != IA_MPEGH_DEC_INIT_NONFATAL_INSUFFICIENT_INPUT_BYTES) goto cleanup;
        if (!out->ui_init_done && !got && !out->i_bytes_consumed) goto cleanup;
    } while (!out->ui_init_done);
    {
        ia_dec_data_struct *data = decoder->p_state_mpeghd->pstr_dec_data;
        ia_audio_specific_config_struct *asc = &data->str_frame_data.str_audio_specific_config;
        ia_signals_3d *signals = &asc->str_usac_config.signals_3d;
        objects = signals->num_audio_obj;
        if (asc->sampling_frequency != 48000) { fputs("Only 48000 Hz is supported.\n", stderr); goto cleanup; }
        if (signals->num_ch || signals->num_hoa_transport_ch || signals->num_obj_based_groups != 1 ||
            objects < 1 || objects > 32) {
            fputs("Only a single MPEG-H object group is supported; channel/mixed/HOA scenes are rejected.\n", stderr);
            goto cleanup;
        }
        printf("{\"type\":\"config\",\"objects\":%d,\"rate\":48000}\n", objects);
        fflush(stdout);
    }
    pcm = fopen("objects.pcm", "wb");
    meta = fopen("oam.jsonl", "wb");
    original = fopen("oam.bs", "wb");
    if (!pcm || !meta || !original) goto cleanup;
    for (;;) {
        got = refill(file, input, capacity, &available, out->i_bytes_consumed);
        if (got < 0) goto cleanup;
        if (!available) break;
        in->num_inp_bytes = available;
        error = ia_mpegh_dec_execute(decoder, in, out);
        if (error) goto cleanup;
        if (!out->i_bytes_consumed) { fputs("Decoder made no progress.\n", stderr); goto cleanup; }
        /* External payload length can retain its prior value for non-audio
         * packets. The public output length identifies an actual decoded AU. */
        if (!out->num_out_bytes) continue;
        /* The upstream writer is a fixed 768-byte ring. Do not read past it
         * or publish a wrapped binary metadata backup. */
        if (out->oam_md_payload_length < 0 || out->oam_md_payload_length > 768) {
            fputs("External OAM exceeds upstream 768-byte buffer; unsupported metadata density.\n", stderr);
            goto cleanup;
        }
        ia_dec_data_struct *data = decoder->p_state_mpeghd->pstr_dec_data;
        ia_signals_3d *signals = &data->str_frame_data.str_audio_specific_config.str_usac_config.signals_3d;
        if (data->str_frame_data.str_audio_specific_config.sampling_frequency != 48000 ||
            signals->num_obj_based_groups != 1 ||
            signals->num_audio_obj != objects || signals->num_ch || signals->num_hoa_transport_ch ||
            out->pcm_payload_length != objects * 1024 * 3 || out->num_preroll > 1) {
            fprintf(stderr, "Unsupported configuration/preroll/PCM length: objects=%u channels=%u hoa=%u bytes=%d preroll=%d\n",
                signals->num_audio_obj, signals->num_ch, signals->num_hoa_transport_ch, out->pcm_payload_length, out->num_preroll);
            goto cleanup;
        }
        if (snapshot(meta, decoder, (long long)frames * 1024, objects)) goto cleanup;
        if (fwrite(in->ptr_ext_ren_pcm_buf, 1, out->pcm_payload_length, pcm) != out->pcm_payload_length) goto cleanup;
        if (fwrite(in->ptr_ext_ren_oam_data_buf, 1, out->oam_md_payload_length, original) != out->oam_md_payload_length) goto cleanup;
        ++frames;
        if (!(frames % 128)) { printf("{\"type\":\"decoded\",\"frames\":%d}\n", frames); fflush(stdout); }
    }
    if (!frames) { fputs("No decoded object frames.\n", stderr); goto cleanup; }
    result = 0;
    printf("{\"type\":\"decoded\",\"frames\":%d}\n", frames);
cleanup:
    if (error) fprintf(stderr, "libmpegh error 0x%08x\n", (unsigned int)error);
    if (pcm && fclose(pcm)) result = 1;
    if (meta && fclose(meta)) result = 1;
    if (original && fclose(original)) result = 1;
    if (created) ia_mpegh_dec_delete(out);
    if (file) impeghd_mp4_fw_close(file);
    free(in->ptr_ext_ren_ch_data_buf);
    free(in->ptr_ext_ren_oam_data_buf);
    free(in->ptr_ext_ren_hoa_data_buf);
    free(in->ptr_ext_ren_pcm_buf);
    return result;
}
