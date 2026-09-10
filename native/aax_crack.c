/*
 * aax_crack — brute-force an Audible .aax file's 4-byte activation bytes from its
 * 20-byte DRM "file checksum" (the value ffprobe prints).
 *
 * Algorithm ported verbatim from FFmpeg libavformat/mov.c::mov_read_adrm:
 *
 *   intermediate_key = SHA1( fixed_key[16] || activation_bytes[4] )
 *   intermediate_iv  = SHA1( fixed_key[16] || intermediate_key[20] || activation_bytes[4] )
 *   checksum         = SHA1( intermediate_key[0:16] || intermediate_iv[0:16] )
 *
 * The activation bytes are correct when `checksum` equals the file's stored
 * 20-byte checksum. Verification uses SHA1 only (no AES), so the full 2^32 space
 * is cheap to search. Every hash here is a single <= 55-byte block, so we use a
 * dedicated single-block SHA1 (no streaming/padding overhead).
 *
 * Usage:
 *   aax_crack <checksum-40-hex> [--threads N] [--start HEX] [--end HEX] [--quiet]
 *
 * On success: prints the 8-hex activation bytes to stdout and exits 0.
 * Not found : exits 2.  Usage/other error: exits 1.
 */

#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* ---- single-block SHA1 --------------------------------------------------- */
#define ROL(v, b) (((v) << (b)) | ((v) >> (32 - (b))))

/* SHA1 of a message of length <= 55 bytes (fits in one padded 64-byte block). */
static inline void sha1(const uint8_t *msg, size_t len, uint8_t out[20]) {
    uint8_t block[64];
    uint32_t w[80];
    size_t i;

    for (i = 0; i < len; i++)
        block[i] = msg[i];
    block[len] = 0x80;
    for (i = len + 1; i < 56; i++)
        block[i] = 0;
    uint64_t bits = (uint64_t)len * 8;
    block[56] = (uint8_t)(bits >> 56); block[57] = (uint8_t)(bits >> 48);
    block[58] = (uint8_t)(bits >> 40); block[59] = (uint8_t)(bits >> 32);
    block[60] = (uint8_t)(bits >> 24); block[61] = (uint8_t)(bits >> 16);
    block[62] = (uint8_t)(bits >> 8);  block[63] = (uint8_t)(bits);

    for (i = 0; i < 16; i++)
        w[i] = (uint32_t)block[i * 4] << 24 | (uint32_t)block[i * 4 + 1] << 16 |
               (uint32_t)block[i * 4 + 2] << 8 | (uint32_t)block[i * 4 + 3];
    for (i = 16; i < 80; i++)
        w[i] = ROL(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);

    uint32_t a = 0x67452301, b = 0xEFCDAB89, c = 0x98BADCFE, d = 0x10325476, e = 0xC3D2E1F0;
    uint32_t t;
    /* 4 unrolled phases: no per-round branch on the round function / constant. */
#define STEP(F, K, I) do { \
        t = ROL(a, 5) + (F) + e + (K) + w[I]; \
        e = d; d = c; c = ROL(b, 30); b = a; a = t; \
    } while (0)
    for (i = 0; i < 20; i++) STEP((b & c) | ((~b) & d),        0x5A827999u, i);
    for (; i < 40; i++)      STEP(b ^ c ^ d,                   0x6ED9EBA1u, i);
    for (; i < 60; i++)      STEP((b & c) | (b & d) | (c & d), 0x8F1BBCDCu, i);
    for (; i < 80; i++)      STEP(b ^ c ^ d,                   0xCA62C1D6u, i);
#undef STEP
    a += 0x67452301; b += 0xEFCDAB89; c += 0x98BADCFE; d += 0x10325476; e += 0xC3D2E1F0;

    out[0] = (uint8_t)(a >> 24); out[1] = (uint8_t)(a >> 16); out[2] = (uint8_t)(a >> 8); out[3] = (uint8_t)a;
    out[4] = (uint8_t)(b >> 24); out[5] = (uint8_t)(b >> 16); out[6] = (uint8_t)(b >> 8); out[7] = (uint8_t)b;
    out[8] = (uint8_t)(c >> 24); out[9] = (uint8_t)(c >> 16); out[10] = (uint8_t)(c >> 8); out[11] = (uint8_t)c;
    out[12] = (uint8_t)(d >> 24); out[13] = (uint8_t)(d >> 16); out[14] = (uint8_t)(d >> 8); out[15] = (uint8_t)d;
    out[16] = (uint8_t)(e >> 24); out[17] = (uint8_t)(e >> 16); out[18] = (uint8_t)(e >> 8); out[19] = (uint8_t)e;
}

/* ---- AAX forward function ------------------------------------------------ */
static const uint8_t FIXED_KEY[16] = {
    0x77, 0x21, 0x4d, 0x4b, 0x19, 0x6a, 0x87, 0xcd,
    0x52, 0x00, 0x45, 0xfd, 0x20, 0xa5, 0x1d, 0x67,
};

/* Compute the 20-byte checksum for the given 32-bit activation value. */
static inline void aax_checksum(uint32_t n, uint8_t out[20]) {
    uint8_t ab[4] = {
        (uint8_t)(n >> 24), (uint8_t)(n >> 16), (uint8_t)(n >> 8), (uint8_t)n,
    };
    uint8_t ik[20], iv[20];
    uint8_t buf[40];

    /* intermediate_key = SHA1(fixed_key || ab) */
    memcpy(buf, FIXED_KEY, 16);
    memcpy(buf + 16, ab, 4);
    sha1(buf, 20, ik);

    /* intermediate_iv = SHA1(fixed_key || ik || ab) */
    memcpy(buf, FIXED_KEY, 16);
    memcpy(buf + 16, ik, 20);
    memcpy(buf + 36, ab, 4);
    sha1(buf, 40, iv);

    /* checksum = SHA1(ik[0:16] || iv[0:16]) */
    memcpy(buf, ik, 16);
    memcpy(buf + 16, iv, 16);
    sha1(buf, 32, out);
}

/* Self-test: the known vector for activation bytes 0x12345678. */
static int self_test(void) {
    static const uint8_t expect[20] = {
        0x9e, 0xb8, 0x75, 0xb4, 0x84, 0xf8, 0x75, 0x10, 0xb8, 0x8e,
        0xb3, 0x89, 0x03, 0x1e, 0x06, 0x79, 0x06, 0x74, 0x19, 0x3e,
    };
    uint8_t cs[20];
    aax_checksum(0x12345678u, cs);
    return memcmp(cs, expect, 20) == 0 ? 0 : -1;
}

/* ---- worker threads ------------------------------------------------------ */
static uint8_t g_target[20];
static atomic_int g_found = 0;
static uint32_t g_result = 0;
static atomic_ullong g_tried = 0;
static int g_quiet = 0;

typedef struct {
    uint64_t start; /* inclusive */
    uint64_t end;   /* inclusive */
} range_t;

static void *worker(void *arg) {
    range_t *r = (range_t *)arg;
    uint8_t cs[20];
    unsigned long long local = 0;
    for (uint64_t i = r->start; i <= r->end; i++) {
        if ((i & 0x3FFFF) == 0) {                 /* ~every 256k candidates */
            if (atomic_load_explicit(&g_found, memory_order_relaxed))
                break;
            atomic_fetch_add_explicit(&g_tried, local, memory_order_relaxed);
            local = 0;
        }
        aax_checksum((uint32_t)i, cs);
        if (memcmp(cs, g_target, 20) == 0) {
            g_result = (uint32_t)i;
            atomic_store_explicit(&g_found, 1, memory_order_relaxed);
            break;
        }
        local++;
    }
    atomic_fetch_add_explicit(&g_tried, local, memory_order_relaxed);
    return NULL;
}

static void *monitor(void *arg) {
    uint64_t total = *(uint64_t *)arg;
    struct timespec ts = {1, 0};
    unsigned long long last = 0;
    while (!atomic_load_explicit(&g_found, memory_order_relaxed)) {
        nanosleep(&ts, NULL);
        unsigned long long tried = atomic_load_explicit(&g_tried, memory_order_relaxed);
        if (total) {
            double pct = 100.0 * (double)tried / (double)total;
            unsigned long long rate = tried - last;
            fprintf(stderr, "\rPROGRESS %6.2f%%  %llu/%llu  (%.1f Mkeys/s)   ",
                    pct, tried, (unsigned long long)total, rate / 1e6);
            fflush(stderr);
        }
        last = tried;
        if (tried >= total)
            break;
    }
    fprintf(stderr, "\n");
    return NULL;
}

static int hexval(int c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int parse_checksum(const char *hex, uint8_t out[20]) {
    if (strlen(hex) != 40) return -1;
    for (int i = 0; i < 20; i++) {
        int hi = hexval(hex[i * 2]), lo = hexval(hex[i * 2 + 1]);
        if (hi < 0 || lo < 0) return -1;
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    return 0;
}

int main(int argc, char **argv) {
    if (self_test() != 0) {
        fprintf(stderr, "error: SHA1 self-test failed (build is broken)\n");
        return 1;
    }

    if (argc < 2) {
        fprintf(stderr,
                "usage: %s <checksum-40-hex> [--threads N] [--start HEX] [--end HEX] [--quiet]\n",
                argv[0]);
        return 1;
    }

    const char *checksum_hex = argv[1];
    /* Allow `--selftest` as a standalone verification hook. */
    if (!strcmp(checksum_hex, "--selftest")) {
        printf("ok\n");
        return 0;
    }

    long nthreads = 0;
    uint64_t start = 0, end = 0xFFFFFFFFULL;

    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--threads") && i + 1 < argc) {
            nthreads = strtol(argv[++i], NULL, 10);
        } else if (!strcmp(argv[i], "--start") && i + 1 < argc) {
            start = strtoull(argv[++i], NULL, 16);
        } else if (!strcmp(argv[i], "--end") && i + 1 < argc) {
            end = strtoull(argv[++i], NULL, 16);
        } else if (!strcmp(argv[i], "--quiet")) {
            g_quiet = 1;
        } else {
            fprintf(stderr, "unknown argument: %s\n", argv[i]);
            return 1;
        }
    }

    if (parse_checksum(checksum_hex, g_target) != 0) {
        fprintf(stderr, "error: checksum must be exactly 40 hex characters\n");
        return 1;
    }
    if (end > 0xFFFFFFFFULL) end = 0xFFFFFFFFULL;
    if (start > end) {
        fprintf(stderr, "error: --start must be <= --end\n");
        return 1;
    }
    if (nthreads <= 0) {
        long n = sysconf(_SC_NPROCESSORS_ONLN);
        nthreads = (n > 0) ? n : 4;
    }

    uint64_t total = end - start + 1;
    if ((uint64_t)nthreads > total) nthreads = (long)total;

    pthread_t *threads = calloc(nthreads, sizeof(*threads));
    range_t *ranges = calloc(nthreads, sizeof(*ranges));
    if (!threads || !ranges) {
        fprintf(stderr, "error: out of memory\n");
        free(threads); free(ranges);
        return 1;
    }

    uint64_t chunk = total / (uint64_t)nthreads;
    uint64_t rem = total % (uint64_t)nthreads;
    uint64_t cur = start;
    for (long t = 0; t < nthreads; t++) {
        uint64_t this_chunk = chunk + (((uint64_t)t < rem) ? 1 : 0);
        ranges[t].start = cur;
        ranges[t].end = cur + this_chunk - 1;
        cur += this_chunk;
    }

    pthread_t mon;
    int have_mon = !g_quiet && (pthread_create(&mon, NULL, monitor, &total) == 0);

    for (long t = 0; t < nthreads; t++)
        pthread_create(&threads[t], NULL, worker, &ranges[t]);
    for (long t = 0; t < nthreads; t++)
        pthread_join(threads[t], NULL);

    if (have_mon)
        pthread_join(mon, NULL);

    free(threads);
    free(ranges);

    if (atomic_load(&g_found)) {
        printf("%08x\n", g_result);
        return 0;
    }
    return 2;
}
