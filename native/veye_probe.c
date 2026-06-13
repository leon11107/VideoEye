/*
 * veye_probe — decode a video stream with patched FFmpeg and dump per-frame
 * block analysis (AV_FRAME_DATA_VEYE_BLOCKINFO) to a compact sidecar file.
 *
 * Usage: veye_probe <input> <output.veblk>
 *
 * Sidecar layout (native-endian):
 *   file header:
 *     u32 magic   = 'VEYE' little-endian
 *     u32 version = 1
 *     u32 n_frames            (patched after the decode loop)
 *   then n_frames entries, in decode/display order:
 *     u32 frame_index
 *     u32 payload_size
 *     u8  payload[payload_size]   (raw VeyeBlockInfo side-data buffer)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/frame.h>

#define SIDECAR_MAGIC   0x45594556u  /* 'VEYE' little-endian -> bytes V E Y E */
#define SIDECAR_VERSION 1

static int write_u32(FILE *f, uint32_t v)
{
    return fwrite(&v, sizeof(v), 1, f) == 1 ? 0 : -1;
}

int main(int argc, char **argv)
{
    AVFormatContext *fmt = NULL;
    const AVCodec *dec = NULL;
    AVCodecContext *ctx = NULL;
    AVPacket *pkt = NULL;
    AVFrame *frame = NULL;
    FILE *out = NULL;
    int vid = -1, ret = 0;
    uint32_t n_frames = 0, frame_index = 0;

    if (argc != 3) {
        fprintf(stderr, "usage: %s <input> <output.veblk>\n", argv[0]);
        return 2;
    }

    if ((ret = avformat_open_input(&fmt, argv[1], NULL, NULL)) < 0) {
        fprintf(stderr, "open_input failed: %d\n", ret);
        return 1;
    }
    if ((ret = avformat_find_stream_info(fmt, NULL)) < 0) {
        fprintf(stderr, "find_stream_info failed: %d\n", ret);
        goto end;
    }

    vid = av_find_best_stream(fmt, AVMEDIA_TYPE_VIDEO, -1, -1, &dec, 0);
    if (vid < 0 || !dec) {
        fprintf(stderr, "no video stream / decoder\n");
        ret = 1;
        goto end;
    }

    ctx = avcodec_alloc_context3(dec);
    if (!ctx) { ret = 1; goto end; }
    avcodec_parameters_to_context(ctx, fmt->streams[vid]->codecpar);
    /* Single-threaded: deterministic output order, no flush-burst quirks. */
    ctx->thread_count = 1;
    if ((ret = avcodec_open2(ctx, dec, NULL)) < 0) {
        fprintf(stderr, "open2 failed: %d\n", ret);
        goto end;
    }

    out = fopen(argv[2], "wb");
    if (!out) { fprintf(stderr, "cannot open output\n"); ret = 1; goto end; }
    write_u32(out, SIDECAR_MAGIC);
    write_u32(out, SIDECAR_VERSION);
    write_u32(out, 0);  /* n_frames placeholder, patched below */

    pkt = av_packet_alloc();
    frame = av_frame_alloc();
    if (!pkt || !frame) { ret = 1; goto end; }

    for (;;) {
        int got_pkt = (av_read_frame(fmt, pkt) >= 0);
        if (got_pkt && pkt->stream_index != vid) {
            av_packet_unref(pkt);
            continue;
        }
        ret = avcodec_send_packet(ctx, got_pkt ? pkt : NULL);
        if (got_pkt)
            av_packet_unref(pkt);

        while (ret >= 0) {
            ret = avcodec_receive_frame(ctx, frame);
            if (ret == AVERROR(EAGAIN) || ret == AVERROR_EOF)
                break;
            if (ret < 0) { fprintf(stderr, "decode error %d\n", ret); goto end; }

            AVFrameSideData *sd =
                av_frame_get_side_data(frame, AV_FRAME_DATA_VEYE_BLOCKINFO);
            uint32_t size = sd ? (uint32_t)sd->size : 0;
            write_u32(out, frame_index);
            write_u32(out, size);
            if (size)
                fwrite(sd->data, 1, size, out);
            n_frames++;
            frame_index++;
            av_frame_unref(frame);
        }
        if (!got_pkt)
            break;
        ret = 0;
    }

    /* Patch n_frames in the file header. */
    fseek(out, 8, SEEK_SET);
    write_u32(out, n_frames);
    ret = 0;
    fprintf(stderr, "veye_probe: %u frames dumped\n", n_frames);

end:
    if (out) fclose(out);
    av_frame_free(&frame);
    av_packet_free(&pkt);
    avcodec_free_context(&ctx);
    avformat_close_input(&fmt);
    return ret < 0 ? 1 : 0;
}
