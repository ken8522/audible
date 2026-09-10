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
 * 20-byte checksum. Verification uses SHA1 only (no AES), and every hash is a
 * single <= 55-byte block. Two SHA1 backends are provided:
 *   - a portable scalar single-block implementation (works everywhere), and
 *   - an x86 SHA-NI (SHA extensions) implementation, selected at runtime when the
 *     CPU supports it, which is several times faster.
 *
 * Usage:
 *   aax_crack <checksum-40-hex> [--threads N] [--start HEX] [--end HEX] [--quiet] [--scalar]
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

#if (defined(__x86_64__) || defined(__i386__)) && (defined(__GNUC__) || defined(__clang__))
#define AAX_X86 1
#include <immintrin.h>
#else
#define AAX_X86 0
#endif

/* ---- portable single-block SHA1 ------------------------------------------ */
#define ROL(v, b) (((v) << (b)) | ((v) >> (32 - (b))))

static inline void build_block(const uint8_t *msg, size_t len, uint8_t block[64]) {
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
}

/* SHA1 of a message of length <= 55 bytes (fits in one padded 64-byte block). */
static inline void sha1_scalar(const uint8_t *msg, size_t len, uint8_t out[20]) {
    uint8_t block[64];
    uint32_t w[80];
    size_t i;

    build_block(msg, len, block);
    for (i = 0; i < 16; i++)
        w[i] = (uint32_t)block[i * 4] << 24 | (uint32_t)block[i * 4 + 1] << 16 |
               (uint32_t)block[i * 4 + 2] << 8 | (uint32_t)block[i * 4 + 3];
    for (i = 16; i < 80; i++)
        w[i] = ROL(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);

    uint32_t a = 0x67452301, b = 0xEFCDAB89, c = 0x98BADCFE, d = 0x10325476, e = 0xC3D2E1F0;
    uint32_t t;
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

    uint32_t h[5] = {a, b, c, d, e};
    for (i = 0; i < 20; i++)
        out[i] = (uint8_t)(h[i >> 2] >> ((3 - (i & 3)) * 8));
}

/* ---- x86 SHA-NI single-block SHA1 ---------------------------------------- */
#if AAX_X86
/* One-block SHA1 compression using Intel SHA extensions
 * (after the public-domain implementation by Jeffrey Walton / Intel). */
__attribute__((target("sha,sse4.1,ssse3")))
static void sha1_ni_block(uint32_t state[5], const uint8_t data[64]) {
    __m128i ABCD, ABCD_SAVE, E0, E0_SAVE, E1;
    __m128i MSG0, MSG1, MSG2, MSG3;
    const __m128i MASK = _mm_set_epi64x(0x0001020304050607ULL, 0x08090a0b0c0d0e0fULL);

    ABCD = _mm_loadu_si128((const __m128i *)state);
    E0 = _mm_set_epi32((int)state[4], 0, 0, 0);
    ABCD = _mm_shuffle_epi32(ABCD, 0x1B);

    ABCD_SAVE = ABCD;
    E0_SAVE = E0;

    MSG0 = _mm_shuffle_epi8(_mm_loadu_si128((const __m128i *)(data + 0)), MASK);
    E0 = _mm_add_epi32(E0, MSG0);
    E1 = ABCD;
    ABCD = _mm_sha1rnds4_epu32(ABCD, E0, 0);

    MSG1 = _mm_shuffle_epi8(_mm_loadu_si128((const __m128i *)(data + 16)), MASK);
    E1 = _mm_sha1nexte_epu32(E1, MSG1);
    E0 = ABCD;
    ABCD = _mm_sha1rnds4_epu32(ABCD, E1, 0);
    MSG0 = _mm_sha1msg1_epu32(MSG0, MSG1);

    MSG2 = _mm_shuffle_epi8(_mm_loadu_si128((const __m128i *)(data + 32)), MASK);
    E0 = _mm_sha1nexte_epu32(E0, MSG2);
    E1 = ABCD;
    ABCD = _mm_sha1rnds4_epu32(ABCD, E0, 0);
    MSG1 = _mm_sha1msg1_epu32(MSG1, MSG2);
    MSG0 = _mm_xor_si128(MSG0, MSG2);

    MSG3 = _mm_shuffle_epi8(_mm_loadu_si128((const __m128i *)(data + 48)), MASK);
    E1 = _mm_sha1nexte_epu32(E1, MSG3);
    E0 = ABCD;
    MSG0 = _mm_sha1msg2_epu32(MSG0, MSG3);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E1, 0);
    MSG2 = _mm_sha1msg1_epu32(MSG2, MSG3);
    MSG1 = _mm_xor_si128(MSG1, MSG3);

    E0 = _mm_sha1nexte_epu32(E0, MSG0);
    E1 = ABCD;
    MSG1 = _mm_sha1msg2_epu32(MSG1, MSG0);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E0, 0);
    MSG3 = _mm_sha1msg1_epu32(MSG3, MSG0);
    MSG2 = _mm_xor_si128(MSG2, MSG0);

    E1 = _mm_sha1nexte_epu32(E1, MSG1);
    E0 = ABCD;
    MSG2 = _mm_sha1msg2_epu32(MSG2, MSG1);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E1, 1);
    MSG0 = _mm_sha1msg1_epu32(MSG0, MSG1);
    MSG3 = _mm_xor_si128(MSG3, MSG1);

    E0 = _mm_sha1nexte_epu32(E0, MSG2);
    E1 = ABCD;
    MSG3 = _mm_sha1msg2_epu32(MSG3, MSG2);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E0, 1);
    MSG1 = _mm_sha1msg1_epu32(MSG1, MSG2);
    MSG0 = _mm_xor_si128(MSG0, MSG2);

    E1 = _mm_sha1nexte_epu32(E1, MSG3);
    E0 = ABCD;
    MSG0 = _mm_sha1msg2_epu32(MSG0, MSG3);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E1, 1);
    MSG2 = _mm_sha1msg1_epu32(MSG2, MSG3);
    MSG1 = _mm_xor_si128(MSG1, MSG3);

    E0 = _mm_sha1nexte_epu32(E0, MSG0);
    E1 = ABCD;
    MSG1 = _mm_sha1msg2_epu32(MSG1, MSG0);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E0, 1);
    MSG3 = _mm_sha1msg1_epu32(MSG3, MSG0);
    MSG2 = _mm_xor_si128(MSG2, MSG0);

    E1 = _mm_sha1nexte_epu32(E1, MSG1);
    E0 = ABCD;
    MSG2 = _mm_sha1msg2_epu32(MSG2, MSG1);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E1, 1);
    MSG0 = _mm_sha1msg1_epu32(MSG0, MSG1);
    MSG3 = _mm_xor_si128(MSG3, MSG1);

    E0 = _mm_sha1nexte_epu32(E0, MSG2);
    E1 = ABCD;
    MSG3 = _mm_sha1msg2_epu32(MSG3, MSG2);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E0, 2);
    MSG1 = _mm_sha1msg1_epu32(MSG1, MSG2);
    MSG0 = _mm_xor_si128(MSG0, MSG2);

    E1 = _mm_sha1nexte_epu32(E1, MSG3);
    E0 = ABCD;
    MSG0 = _mm_sha1msg2_epu32(MSG0, MSG3);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E1, 2);
    MSG2 = _mm_sha1msg1_epu32(MSG2, MSG3);
    MSG1 = _mm_xor_si128(MSG1, MSG3);

    E0 = _mm_sha1nexte_epu32(E0, MSG0);
    E1 = ABCD;
    MSG1 = _mm_sha1msg2_epu32(MSG1, MSG0);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E0, 2);
    MSG3 = _mm_sha1msg1_epu32(MSG3, MSG0);
    MSG2 = _mm_xor_si128(MSG2, MSG0);

    E1 = _mm_sha1nexte_epu32(E1, MSG1);
    E0 = ABCD;
    MSG2 = _mm_sha1msg2_epu32(MSG2, MSG1);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E1, 2);
    MSG0 = _mm_sha1msg1_epu32(MSG0, MSG1);
    MSG3 = _mm_xor_si128(MSG3, MSG1);

    E0 = _mm_sha1nexte_epu32(E0, MSG2);
    E1 = ABCD;
    MSG3 = _mm_sha1msg2_epu32(MSG3, MSG2);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E0, 2);
    MSG1 = _mm_sha1msg1_epu32(MSG1, MSG2);
    MSG0 = _mm_xor_si128(MSG0, MSG2);

    E1 = _mm_sha1nexte_epu32(E1, MSG3);
    E0 = ABCD;
    MSG0 = _mm_sha1msg2_epu32(MSG0, MSG3);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E1, 3);
    MSG2 = _mm_sha1msg1_epu32(MSG2, MSG3);
    MSG1 = _mm_xor_si128(MSG1, MSG3);

    E0 = _mm_sha1nexte_epu32(E0, MSG0);
    E1 = ABCD;
    MSG1 = _mm_sha1msg2_epu32(MSG1, MSG0);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E0, 3);
    MSG3 = _mm_sha1msg1_epu32(MSG3, MSG0);
    MSG2 = _mm_xor_si128(MSG2, MSG0);

    E1 = _mm_sha1nexte_epu32(E1, MSG1);
    E0 = ABCD;
    MSG2 = _mm_sha1msg2_epu32(MSG2, MSG1);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E1, 3);
    MSG3 = _mm_xor_si128(MSG3, MSG1);

    E0 = _mm_sha1nexte_epu32(E0, MSG2);
    E1 = ABCD;
    MSG3 = _mm_sha1msg2_epu32(MSG3, MSG2);
    ABCD = _mm_sha1rnds4_epu32(ABCD, E0, 3);

    E1 = _mm_sha1nexte_epu32(E1, MSG3);
    E0 = ABCD;
    ABCD = _mm_sha1rnds4_epu32(ABCD, E1, 3);

    E0 = _mm_sha1nexte_epu32(E0, E0_SAVE);
    ABCD = _mm_add_epi32(ABCD, ABCD_SAVE);

    ABCD = _mm_shuffle_epi32(ABCD, 0x1B);
    _mm_storeu_si128((__m128i *)state, ABCD);
    state[4] = (uint32_t)_mm_extract_epi32(E0, 3);
}

__attribute__((target("sha,sse4.1,ssse3")))
static inline void sha1_shani(const uint8_t *msg, size_t len, uint8_t out[20]) {
    uint8_t block[64];
    build_block(msg, len, block);
    uint32_t state[5] = {0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0};
    sha1_ni_block(state, block);
    for (int i = 0; i < 20; i++)
        out[i] = (uint8_t)(state[i >> 2] >> ((3 - (i & 3)) * 8));
}
#endif /* AAX_X86 */

/* ---- AAX forward function (templated over the SHA1 backend) -------------- */
static const uint8_t FIXED_KEY[16] = {
    0x77, 0x21, 0x4d, 0x4b, 0x19, 0x6a, 0x87, 0xcd,
    0x52, 0x00, 0x45, 0xfd, 0x20, 0xa5, 0x1d, 0x67,
};

#define DEFINE_CHECKSUM(NAME, SHA1FN) \
    static inline void NAME(uint32_t n, uint8_t out[20]) { \
        uint8_t ab[4] = {(uint8_t)(n >> 24), (uint8_t)(n >> 16), (uint8_t)(n >> 8), (uint8_t)n}; \
        uint8_t ik[20], iv[20], buf[40]; \
        memcpy(buf, FIXED_KEY, 16); memcpy(buf + 16, ab, 4); \
        SHA1FN(buf, 20, ik); \
        memcpy(buf, FIXED_KEY, 16); memcpy(buf + 16, ik, 20); memcpy(buf + 36, ab, 4); \
        SHA1FN(buf, 40, iv); \
        memcpy(buf, ik, 16); memcpy(buf + 16, iv, 16); \
        SHA1FN(buf, 32, out); \
    }

DEFINE_CHECKSUM(aax_checksum_scalar, sha1_scalar)
#if AAX_X86
__attribute__((target("sha,sse4.1,ssse3")))
DEFINE_CHECKSUM(aax_checksum_shani, sha1_shani)
#endif

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

#define DEFINE_WORKER(NAME, CSUM) \
    static void *NAME(void *arg) { \
        range_t *r = (range_t *)arg; \
        uint8_t cs[20]; \
        unsigned long long local = 0; \
        for (uint64_t i = r->start; i <= r->end; i++) { \
            if ((i & 0x3FFFF) == 0) { \
                if (atomic_load_explicit(&g_found, memory_order_relaxed)) break; \
                atomic_fetch_add_explicit(&g_tried, local, memory_order_relaxed); \
                local = 0; \
            } \
            CSUM((uint32_t)i, cs); \
            if (memcmp(cs, g_target, 20) == 0) { \
                g_result = (uint32_t)i; \
                atomic_store_explicit(&g_found, 1, memory_order_relaxed); \
                break; \
            } \
            local++; \
        } \
        atomic_fetch_add_explicit(&g_tried, local, memory_order_relaxed); \
        return NULL; \
    }

DEFINE_WORKER(worker_scalar, aax_checksum_scalar)
#if AAX_X86
__attribute__((target("sha,sse4.1,ssse3")))
DEFINE_WORKER(worker_shani, aax_checksum_shani)
#endif

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

#if AAX_X86
/* Time both backends over a small sample and report whether SHA-NI is actually
 * faster on THIS machine. In some virtualized environments the SHA extension
 * instructions trap-and-emulate and are far slower than the scalar path, so we
 * never trust the CPUID flag alone. */
static int shani_is_faster(void) {
    const uint64_t M = 20000;
    uint8_t cs[20];
    struct timespec a, b;
    volatile uint8_t sink = 0;

    clock_gettime(CLOCK_MONOTONIC, &a);
    for (uint64_t i = 0; i < M; i++) { aax_checksum_scalar((uint32_t)i, cs); sink ^= cs[0]; }
    clock_gettime(CLOCK_MONOTONIC, &b);
    double ds = (b.tv_sec - a.tv_sec) + (b.tv_nsec - a.tv_nsec) / 1e9;

    clock_gettime(CLOCK_MONOTONIC, &a);
    for (uint64_t i = 0; i < M; i++) { aax_checksum_shani((uint32_t)i, cs); sink ^= cs[0]; }
    clock_gettime(CLOCK_MONOTONIC, &b);
    double dn = (b.tv_sec - a.tv_sec) + (b.tv_nsec - a.tv_nsec) / 1e9;

    (void)sink;
    return dn < ds;
}
#endif

/* Self-test the given checksum backend against the known vector for 0x12345678. */
static int self_test(void (*csum)(uint32_t, uint8_t[20])) {
    static const uint8_t expect[20] = {
        0x9e, 0xb8, 0x75, 0xb4, 0x84, 0xf8, 0x75, 0x10, 0xb8, 0x8e,
        0xb3, 0x89, 0x03, 0x1e, 0x06, 0x79, 0x06, 0x74, 0x19, 0x3e,
    };
    uint8_t cs[20];
    csum(0x12345678u, cs);
    return memcmp(cs, expect, 20) == 0 ? 0 : -1;
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
    if (argc < 2) {
        fprintf(stderr,
                "usage: %s <checksum-40-hex> [--threads N] [--start HEX] [--end HEX] "
                "[--quiet] [--scalar]\n", argv[0]);
        return 1;
    }

    const char *checksum_hex = argv[1];

    /* Hidden micro-benchmark: time each backend single-threaded. */
    if (!strcmp(checksum_hex, "--bench")) {
        const uint64_t N = 3000000;
        uint8_t cs[20];
        struct timespec a, b;
        clock_gettime(CLOCK_MONOTONIC, &a);
        for (uint64_t i = 0; i < N; i++) aax_checksum_scalar((uint32_t)i, cs);
        clock_gettime(CLOCK_MONOTONIC, &b);
        double ds = (b.tv_sec - a.tv_sec) + (b.tv_nsec - a.tv_nsec) / 1e9;
        printf("scalar: %.2f Mkeys/s (%.0f ns/key)\n", N / ds / 1e6, ds / N * 1e9);
#if AAX_X86
        if (__builtin_cpu_supports("sha")) {
            clock_gettime(CLOCK_MONOTONIC, &a);
            for (uint64_t i = 0; i < N; i++) aax_checksum_shani((uint32_t)i, cs);
            clock_gettime(CLOCK_MONOTONIC, &b);
            double dn = (b.tv_sec - a.tv_sec) + (b.tv_nsec - a.tv_nsec) / 1e9;
            printf("sha-ni: %.2f Mkeys/s (%.0f ns/key)\n", N / dn / 1e6, dn / N * 1e9);
        } else {
            printf("sha-ni: (cpu lacks SHA extensions)\n");
        }
#endif
        return 0;
    }

    long nthreads = 0;
    uint64_t start = 0, end = 0xFFFFFFFFULL;
    int force_scalar = 0;
    int selftest_only = !strcmp(checksum_hex, "--selftest");

    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--threads") && i + 1 < argc) {
            nthreads = strtol(argv[++i], NULL, 10);
        } else if (!strcmp(argv[i], "--start") && i + 1 < argc) {
            start = strtoull(argv[++i], NULL, 16);
        } else if (!strcmp(argv[i], "--end") && i + 1 < argc) {
            end = strtoull(argv[++i], NULL, 16);
        } else if (!strcmp(argv[i], "--quiet")) {
            g_quiet = 1;
        } else if (!strcmp(argv[i], "--scalar")) {
            force_scalar = 1;
        } else {
            fprintf(stderr, "unknown argument: %s\n", argv[i]);
            return 1;
        }
    }

    /* Select the fastest available backend, unless forced to scalar. We calibrate
     * rather than trust CPUID, because emulated SHA extensions can be far slower. */
    void (*csum_fn)(uint32_t, uint8_t[20]) = aax_checksum_scalar;
    void *(*worker_fn)(void *) = worker_scalar;
    const char *backend = "scalar";
#if AAX_X86
    if (!force_scalar && __builtin_cpu_supports("sha") && shani_is_faster()) {
        csum_fn = aax_checksum_shani;
        worker_fn = worker_shani;
        backend = "sha-ni";
    }
#else
    (void)force_scalar;
#endif

    if (self_test(csum_fn) != 0) {
        fprintf(stderr, "error: SHA1 self-test failed for backend '%s' (build is broken)\n",
                backend);
        return 1;
    }
    if (selftest_only) {
        printf("ok %s\n", backend);
        return 0;
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

    if (!g_quiet)
        fprintf(stderr, "backend: %s, threads: %ld\n", backend, nthreads);

    pthread_t mon;
    int have_mon = !g_quiet && (pthread_create(&mon, NULL, monitor, &total) == 0);

    for (long t = 0; t < nthreads; t++)
        pthread_create(&threads[t], NULL, worker_fn, &ranges[t]);
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
