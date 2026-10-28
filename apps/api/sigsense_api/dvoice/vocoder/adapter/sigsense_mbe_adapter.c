/*
 * SigSense vocoder adapter, ABI version 1, on top of an mbelib-style decoder.
 *
 * This file is original SigSense code. It contains NO codec implementation: it
 * only calls the public mbelib API (mbelib.h). The mbelib sources are NOT part
 * of this repository; scripts/build_mbe_adapter.py downloads them on the
 * user's own machine, only after an explicit confirmation, and compiles them
 * together with this file into a local libsigsense_mbe.so that is for that
 * user's own use (see docs/digital-voice-codec-setup.md).
 *
 * ABI (see backends/external.py for the authoritative description):
 *   int         sigsense_vocoder_abi_version(void);
 *   void*       sigsense_vocoder_create(int codec);
 *   void        sigsense_vocoder_destroy(void *h);
 *   int         sigsense_vocoder_decode(void *h, const uint8_t *bits, int nbits,
 *                                       int16_t *pcm, int pcm_cap);
 *   int         sigsense_vocoder_conceal(void *h, int16_t *pcm, int pcm_cap);
 *   const char *sigsense_vocoder_describe(void);
 *
 * `bits` are FEC-DECODED parameter bits, one byte per bit (0 or 1): 88 for IMBE
 * and 49 for AMBE / AMBE+2. That is exactly what mbelib's "...Data" entry points
 * take, so none of mbelib's own FEC (ecc.c) is used here: SigSense's protocol
 * layer already corrected the frame and knows how many errors it fixed.
 *
 * Return values: samples written (160 = 20 ms at 8 kHz) on success, otherwise
 *   -1 invalid handle or null pointer
 *   -2 wrong number of bits for this codec
 *   -3 output buffer smaller than one frame
 *
 * Thread safety: every handle owns all of its state, so different handles may be
 * used from different threads. One handle must not be used by two threads at
 * once (SigSense serialises that). mbelib's synthesiser draws unvoiced noise
 * from the C library's rand(), which is shared but safe to call concurrently.
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "mbelib.h"

#define SIGSENSE_ABI_VERSION 1
#define FRAME_SAMPLES 160

/* codec ids from the ABI */
#define CODEC_IMBE_7200X4400 1
#define CODEC_IMBE_7100X4400 2
#define CODEC_AMBE_3600X2400 3
#define CODEC_AMBE2_3600X2450 4

/*
 * mbelib's "unvoiced quality" knob (1..64; values outside that range are
 * clamped by mbelib). 3 is the long-standing default in mbelib-based players:
 * it trades a little unvoiced fidelity for much less CPU.
 */
#define UV_QUALITY 3

/* After this many repeated/lost frames in a row, mbelib mutes. Matches its own
 * decoders' limit so adapter concealment behaves like a damaged frame would. */
#define MAX_REPEAT 3

struct handle {
    int codec;
    mbe_parms cur;
    mbe_parms prev;
    mbe_parms prev_enhanced;
};

static int bits_for(int codec)
{
    switch (codec) {
    case CODEC_IMBE_7200X4400:
    case CODEC_IMBE_7100X4400:
        return 88;
    case CODEC_AMBE_3600X2400:
    case CODEC_AMBE2_3600X2450:
        return 49;
    default:
        return 0;
    }
}

int sigsense_vocoder_abi_version(void)
{
    return SIGSENSE_ABI_VERSION;
}

void *sigsense_vocoder_create(int codec)
{
    struct handle *h;

    if (bits_for(codec) == 0)
        return NULL;
    h = (struct handle *)calloc(1, sizeof(*h));
    if (!h)
        return NULL;
    h->codec = codec;
    mbe_initMbeParms(&h->cur, &h->prev, &h->prev_enhanced);
    return h;
}

void sigsense_vocoder_destroy(void *handle)
{
    free(handle);
}

int sigsense_vocoder_decode(void *handle, const uint8_t *bits, int nbits,
                            int16_t *pcm, int pcm_cap)
{
    struct handle *h = (struct handle *)handle;
    char data[88];
    char err_str[64];
    int errs = 0;
    int errs2 = 0; /* FEC errors already fixed upstream; the ABI doesn't carry the count */
    short out[FRAME_SAMPLES];
    int i;

    if (!h || !bits || !pcm)
        return -1;
    if (nbits != bits_for(h->codec))
        return -2;
    if (pcm_cap < FRAME_SAMPLES)
        return -3;

    for (i = 0; i < nbits; i++)
        data[i] = (char)(bits[i] & 1);
    err_str[0] = 0;

    switch (h->codec) {
    case CODEC_IMBE_7200X4400:
        mbe_processImbe4400Data(out, &errs, &errs2, err_str, data, &h->cur, &h->prev,
                                &h->prev_enhanced, UV_QUALITY);
        break;
    case CODEC_IMBE_7100X4400:
        /* The 7100x4400 layout carries the same parameters in a different bit
         * order; mbelib converts it to the 7200x4400 order first. */
        mbe_convertImbe7100to7200(data);
        mbe_processImbe4400Data(out, &errs, &errs2, err_str, data, &h->cur, &h->prev,
                                &h->prev_enhanced, UV_QUALITY);
        break;
    case CODEC_AMBE_3600X2400:
        mbe_processAmbe2400Data(out, &errs, &errs2, err_str, data, &h->cur, &h->prev,
                                &h->prev_enhanced, UV_QUALITY);
        break;
    case CODEC_AMBE2_3600X2450:
        mbe_processAmbe2450Data(out, &errs, &errs2, err_str, data, &h->cur, &h->prev,
                                &h->prev_enhanced, UV_QUALITY);
        break;
    default:
        return -1;
    }

    for (i = 0; i < FRAME_SAMPLES; i++)
        pcm[i] = (int16_t)out[i];
    return FRAME_SAMPLES;
}

/*
 * Lost-frame fill: repeat the previous speech parameters (as mbelib does for a
 * damaged frame), then mute once several frames in a row have been lost.
 */
int sigsense_vocoder_conceal(void *handle, int16_t *pcm, int pcm_cap)
{
    struct handle *h = (struct handle *)handle;
    short out[FRAME_SAMPLES];
    int i;

    if (!h || !pcm)
        return -1;
    if (pcm_cap < FRAME_SAMPLES)
        return -3;

    mbe_useLastMbeParms(&h->cur, &h->prev);
    h->cur.repeat++;
    if (h->cur.repeat <= MAX_REPEAT) {
        mbe_moveMbeParms(&h->cur, &h->prev);
        mbe_spectralAmpEnhance(&h->cur);
        mbe_synthesizeSpeech(out, &h->cur, &h->prev_enhanced, UV_QUALITY);
        mbe_moveMbeParms(&h->cur, &h->prev_enhanced);
    } else {
        mbe_synthesizeSilence(out);
        mbe_initMbeParms(&h->cur, &h->prev, &h->prev_enhanced);
    }
    for (i = 0; i < FRAME_SAMPLES; i++)
        pcm[i] = (int16_t)out[i];
    return FRAME_SAMPLES;
}

const char *sigsense_vocoder_describe(void)
{
    return "SigSense mbelib adapter v1 (mbelib " MBELIB_VERSION ")";
}
