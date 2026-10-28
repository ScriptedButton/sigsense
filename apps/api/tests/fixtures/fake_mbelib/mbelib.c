#include <math.h>
#include <string.h>
#include "mbelib.h"
void mbe_initMbeParms(mbe_parms *cur, mbe_parms *prev, mbe_parms *prev_enh) {
    memset(cur, 0, sizeof *cur); memset(prev, 0, sizeof *prev); memset(prev_enh, 0, sizeof *prev_enh);
}
void mbe_moveMbeParms(mbe_parms *cur, mbe_parms *prev) { *prev = *cur; }
void mbe_useLastMbeParms(mbe_parms *cur, mbe_parms *prev) { int r = prev->repeat; *cur = *prev; cur->repeat = r; }
void mbe_spectralAmpEnhance(mbe_parms *cur) { (void)cur; }
void mbe_synthesizeSilence(short *aout) { memset(aout, 0, 160 * sizeof(short)); }
/* tone at w0 radians/sample, phase carried in PHIl[0] so frames join smoothly */
void mbe_synthesizeSpeech(short *aout, mbe_parms *cur, mbe_parms *prev, int uvq) {
    float ph = prev->PHIl[0]; int n; (void)uvq;
    for (n = 0; n < 160; n++) { aout[n] = (short)(8000.0f * sinf(ph)); ph += cur->w0; }
    cur->PHIl[0] = fmodf(ph, 6.2831853f);
}
void mbe_convertImbe7100to7200(char *d) { int i; for (i = 0; i < 44; i++) { char t = d[i]; d[i] = d[87 - i]; d[87 - i] = t; } }
static void process(short *aout, char *d, int nbits, mbe_parms *cur, mbe_parms *prev, mbe_parms *enh) {
    int i, v = 0;
    for (i = 0; i < 6 && i < nbits; i++) v = (v << 1) | (d[i] & 1);
    cur->w0 = 0.05f + 0.01f * (float)v;       /* 0.05 .. 0.68 rad/sample */
    cur->repeat = 0;
    mbe_moveMbeParms(cur, prev);
    mbe_synthesizeSpeech(aout, cur, enh, 3);
    mbe_moveMbeParms(cur, enh);
}
#define DEF(name, N) void name(short *aout, int *errs, int *errs2, char *e, char d[N], mbe_parms *c, mbe_parms *p, mbe_parms *x, int u) { (void)errs; (void)errs2; (void)u; e[0] = 0; process(aout, d, N, c, p, x); }
DEF(mbe_processImbe4400Data, 88)
DEF(mbe_processAmbe2400Data, 49)
DEF(mbe_processAmbe2450Data, 49)
