/* TEST STAND-IN with mbelib's function signatures. Not a codec: it turns the
 * first parameter bits into a sine tone so adapter/ABI tests can run offline. */
#ifndef FAKE_MBELIB_H
#define FAKE_MBELIB_H
#define MBELIB_VERSION "fake-test"
typedef struct mbe_parameters {
    float w0; int L; int K; int Vl[57]; float Ml[57]; float log2Ml[57];
    float PHIl[57]; float PSIl[57]; float gamma; int un; int repeat;
} mbe_parms;
void mbe_initMbeParms(mbe_parms *cur, mbe_parms *prev, mbe_parms *prev_enh);
void mbe_moveMbeParms(mbe_parms *cur, mbe_parms *prev);
void mbe_useLastMbeParms(mbe_parms *cur, mbe_parms *prev);
void mbe_spectralAmpEnhance(mbe_parms *cur);
void mbe_synthesizeSpeech(short *aout, mbe_parms *cur, mbe_parms *prev, int uvquality);
void mbe_synthesizeSilence(short *aout);
void mbe_convertImbe7100to7200(char *imbe_d);
void mbe_processImbe4400Data(short *aout, int *errs, int *errs2, char *err_str, char d[88], mbe_parms *cur, mbe_parms *prev, mbe_parms *prev_enh, int uvq);
void mbe_processAmbe2400Data(short *aout, int *errs, int *errs2, char *err_str, char d[49], mbe_parms *cur, mbe_parms *prev, mbe_parms *prev_enh, int uvq);
void mbe_processAmbe2450Data(short *aout, int *errs, int *errs2, char *err_str, char d[49], mbe_parms *cur, mbe_parms *prev, mbe_parms *prev_enh, int uvq);
#endif
