/* swarmstate-node.c — self-contained SwarmState mesh node with a local
 * web dashboard.  Reuses the shared C mesh base (libmesh) for the
 * peer; links the precompiled swarmstate-kernel.dll for kernel
 * identity + sysinfo; detects a local Ollama and serves inference
 * requests through it when present.  Zero runtime dependencies.
 *
 * Builds for Windows (mingw-w64, winsock) and POSIX (gcc, sockets):
 *   win/build.sh   -- both
 * Transport is deliberately thin (the C mesh base is stateless: the
 * CALLER owns sockets) -- this file is that transport.
 */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>
#include <time.h>

#ifdef _WIN32
#  include <winsock2.h>
#  include <ws2tcpip.h>
#  include <process.h>
typedef SOCKET sock_t;
#  define BAD_SOCK INVALID_SOCKET
#  define CLSOCK(s) closesocket(s)
#else
#  include <sys/socket.h>
#  include <netinet/in.h>
#  include <arpa/inet.h>
#  include <unistd.h>
#  include <fcntl.h>
#  include <errno.h>
#  include <dirent.h>
#  include <sys/stat.h>
typedef int sock_t;
#  define BAD_SOCK (-1)
#  define CLSOCK(s) close(s)
#endif

#include "mesh.h"       /* shared C mesh base (stateless CRDT peer) */
/* jsmn: JSMN_PARENT_LINKS MUST match mesh.c's compile-time config or
 * the token struct layouts differ and the parse corrupts the token
 * array.  Declarations only: mesh.c (compiled into the same binary /
 * mesh_win.o) provides the non-static jsmn_parse/jsmn_init. */
#define JSMN_PARENT_LINKS
#include "jsmn.h"       /* tiny JSON tokenizer (from the mesh base) */
#include "kernel.h"     /* swarmstate-kernel.dll (ss_version, sysinfo) */

#ifdef SS_EMBED_INFER
#  include "llama_infer.h"   /* embedded llama.cpp inference (ss_infer_*) */
#endif

#ifdef _WIN32
#  include <windows.h>
typedef CRITICAL_SECTION ss_mutex;
#  define MUTEX_INIT(m) InitializeCriticalSection(m)
#  define MUTEX_LOCK(m) EnterCriticalSection(m)
#  define MUTEX_UNLOCK(m) LeaveCriticalSection(m)
typedef unsigned(__stdcall *thr_proc)(void*);
#else
#  include <pthread.h>
typedef pthread_mutex_t ss_mutex;
#  define MUTEX_INIT(m) pthread_mutex_init(m, NULL)
#  define MUTEX_LOCK(m) pthread_mutex_lock(m)
#  define MUTEX_UNLOCK(m) pthread_mutex_unlock(m)
#endif

/* ── node state ─────────────────────────────────────────────────────────── */
typedef struct conn {
    sock_t  s;
    char    peer_name[64];
    char    rbuf[1 << 16];
    size_t  rused;
    int     caught_up;
    struct conn* next;
} conn_t;

typedef struct { char key[96]; int ok, total; } reg_entry_t;
typedef struct { char name[64]; char models[512]; double load; time_t ts; } cap_t;

typedef struct {
    mesh_peer_t* peer;
    char  name[64];
    int   port;
    char  allow[2048];
    char  join_invite[4096];
    int   join_sent;
    char  root[1024];
    struct { char t[96]; char v[64]; } pubs[64]; int pubs_n;
    char  model[64];
    int   demo;
    int   web_port;
    double hash_interval;
    time_t started;

    conn_t* conns;                 /* live connections (all peers) */
    ss_mutex lock;

    char* hist[512];               /* op history ring (catch-up) */
    size_t hist_n, hist_head;

    reg_entry_t reg[256]; int reg_n;
    cap_t caps[32]; int caps_n;

    int   ollama_ok;
    char  ollama_models[1024];
    int   ollama_nmodels;
    time_t ollama_ts;
#ifdef SS_EMBED_INFER
    ss_infer_ctx* embed;           /* embedded llama.cpp ctx (NULL until loaded) */
    char embed_model[512];         /* explicit GGUF path (--embed) */
    ss_mutex embed_lock;           /* guards embed + the request queue */
    char req_query[4096];          /* pending inference request (last wins) */
    char req_id[64];
    int  req_pending;
#endif

    char  kernel_ver[128];
    char  config_json[4096];
    char  pool_label[128];
    uint64_t disk_total, disk_free;
    size_t ops_published, ops_applied, ops_dropped;

    /* sockaddr for outbound peers */
    struct { char host[128]; int port; } out[16]; int out_n;
} node_t;

static node_t N;

/* ── tiny thread + sleep shim (winsock/pthread) ───────────────────────── */
static void ss_sleep_ms(int ms) {
#ifdef _WIN32
    Sleep((DWORD)ms);
#else
    usleep((useconds_t)ms * 1000);
#endif
}
typedef void (*thr_fn)(void*);
typedef struct { thr_fn fn; void* arg; } thr_arg_t;
#ifdef _WIN32
static unsigned __stdcall thr_tramp(void* p) {
    thr_arg_t* t = (thr_arg_t*)p;
    t->fn(t->arg);
    free(t);
    return 0;
}
static void thr_spawn(thr_fn fn, void* arg) {
    thr_arg_t* t = (thr_arg_t*)malloc(sizeof(*t));
    t->fn = fn; t->arg = arg;
    _beginthreadex(NULL, 0, thr_tramp, t, 0, NULL);
}
#else
static void* thr_tramp(void* p) {
    thr_arg_t* t = (thr_arg_t*)p;
    t->fn(t->arg);
    free(t);
    return NULL;
}
static void thr_spawn(thr_fn fn, void* arg) {
    thr_arg_t* t = (thr_arg_t*)malloc(sizeof(*t));
    t->fn = fn; t->arg = arg;
    pthread_t th;
    if (pthread_create(&th, NULL, thr_tramp, t) == 0) pthread_detach(th);
}
#endif
static void* ss_memmem(const void* hay, size_t hl, const void* needle, size_t nl) {
    if (nl == 0) return (void*)hay;
    if (nl > hl) return NULL;
    const unsigned char* h = (const unsigned char*)hay;
    const unsigned char* n = (const unsigned char*)needle;
    for (size_t i = 0; i + nl <= hl; i++)
        if (h[i] == n[0] && memcmp(h + i, needle, nl) == 0) return (void*)(h + i);
    return NULL;
}

/* forward decls (defined below) */
static const char* SS_PLAN_SYS;   /* planner system prompt (see ollama_plan) */
static void api_state(char* out, size_t cap);
static char* ollama_plan(const char* query);
static void handle_control(conn_t* c, const char* js, jsmntok_t* t, int ntoks);

/* ── sha256 (compact; public-domain style) ─────────────────────────────── */
typedef struct { uint32_t h[8]; uint64_t len; uint8_t buf[64]; size_t buflen; } sha256_t;
static const uint32_t K256[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
static inline uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }
static void sha256_block(sha256_t* c, const uint8_t* p) {
    uint32_t w[64]; int i;
    for (i = 0; i < 16; i++)
        w[i] = ((uint32_t)p[i*4]<<24)|((uint32_t)p[i*4+1]<<16)|((uint32_t)p[i*4+2]<<8)|p[i*4+3];
    for (i = 16; i < 64; i++) {
        uint32_t s0 = rotr(w[i-15],7)^rotr(w[i-15],18)^(w[i-15]>>3);
        uint32_t s1 = rotr(w[i-2],17)^rotr(w[i-2],19)^(w[i-2]>>10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    uint32_t a=c->h[0],b=c->h[1],cc=c->h[2],d=c->h[3],e=c->h[4],f=c->h[5],g=c->h[6],h=c->h[7];
    for (i = 0; i < 64; i++) {
        uint32_t S1 = rotr(e,6)^rotr(e,11)^rotr(e,25);
        uint32_t ch = (e&f)^((~e)&g);
        uint32_t t1 = h + S1 + ch + K256[i] + w[i];
        uint32_t S0 = rotr(a,2)^rotr(a,13)^rotr(a,22);
        uint32_t mj = (a&b)^(a&cc)^(b&cc);
        uint32_t t2 = S0 + mj;
        h=g; g=f; f=e; e=d+t1; d=cc; cc=b; b=a; a=t1+t2;
    }
    c->h[0]+=a; c->h[1]+=b; c->h[2]+=cc; c->h[3]+=d;
    c->h[4]+=e; c->h[5]+=f; c->h[6]+=g; c->h[7]+=h;
}
static void sha256_init(sha256_t* c) {
    c->h[0]=0x6a09e667;c->h[1]=0xbb67ae85;c->h[2]=0x3c6ef372;c->h[3]=0xa54ff53a;
    c->h[4]=0x510e527f;c->h[5]=0x9b05688c;c->h[6]=0x1f83d9ab;c->h[7]=0x5be0cd19;
    c->len=0; c->buflen=0;
}
static void sha256_update(sha256_t* c, const void* data, size_t n) {
    const uint8_t* p = (const uint8_t*)data;
    c->len += n;
    while (n) {
        size_t take = 64 - c->buflen; if (take > n) take = n;
        memcpy(c->buf + c->buflen, p, take);
        c->buflen += take; p += take; n -= take;
        if (c->buflen == 64) { sha256_block(c, c->buf); c->buflen = 0; }
    }
}
static void sha256_final(sha256_t* c, uint8_t out[32]) {
    uint64_t bits = c->len * 8; int i;
    uint8_t pad = 0x80;
    sha256_update(c, &pad, 1);
    uint8_t z = 0;
    while (c->buflen != 56) sha256_update(c, &z, 1);
    uint8_t lenb[8];
    for (i = 0; i < 8; i++) lenb[i] = (uint8_t)(bits >> (56 - i*8));
    sha256_update(c, lenb, 8);
    for (i = 0; i < 8; i++) { out[i*4]=(uint8_t)(c->h[i]>>24); out[i*4+1]=(uint8_t)(c->h[i]>>16); out[i*4+2]=(uint8_t)(c->h[i]>>8); out[i*4+3]=(uint8_t)c->h[i]; }
}
static void state_hash_hex(char* out, size_t cap) {
    char* js = mesh_peer_state_json(N.peer);
    if (!js) { snprintf(out, cap, "empty"); return; }
    sha256_t c; uint8_t d[32]; int i;
    sha256_init(&c); sha256_update(&c, js, strlen(js)); sha256_final(&c, d);
    for (i = 0; i < 32 && (size_t)(i*2+2) < cap; i++) snprintf(out + i*2, 3, "%02x", d[i]);
    mesh_free(js);
}

/* ── tiny HTTP client (no curl) ────────────────────────────────────────── */
static sock_t tcp_connect(const char* host, int port, int timeout_ms) {
    sock_t s = socket(AF_INET, SOCK_STREAM, 0);
    if (s == BAD_SOCK) return BAD_SOCK;
#ifdef _WIN32
    DWORD tv = (DWORD)timeout_ms;
#else
    struct timeval tv; tv.tv_sec = timeout_ms/1000; tv.tv_usec = (timeout_ms%1000)*1000;
#endif
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char*)&tv, sizeof(tv));
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, (const char*)&tv, sizeof(tv));
    struct sockaddr_in a; memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET; a.sin_port = htons((unsigned short)port);
    a.sin_addr.s_addr = inet_addr(host);
    if (connect(s, (struct sockaddr*)&a, sizeof(a)) != 0) { CLSOCK(s); return BAD_SOCK; }
    return s;
}
static int http_request(const char* method, const char* path, const char* body,
                        char* out, size_t cap, int timeout_ms) {
    sock_t s = tcp_connect("127.0.0.1", 11434, timeout_ms);
    if (s == BAD_SOCK) return -1;
    char hdr[2048];
    if (body) snprintf(hdr, sizeof(hdr),
        "%s %s HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
        "Content-Length: %zu\r\n\r\n", method, path, strlen(body));
    else snprintf(hdr, sizeof(hdr), "%s %s HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n",
                  method, path);
    if (send(s, hdr, (int)strlen(hdr), 0) < 0) { CLSOCK(s); return -1; }
    if (body && send(s, body, (int)strlen(body), 0) < 0) { CLSOCK(s); return -1; }
    size_t used = 0; char buf[8192]; int n;
    while ((n = recv(s, buf, sizeof(buf), 0)) > 0 && used < cap) {
        size_t take = (size_t)n; if (take > cap - used) take = cap - used;
        memcpy(out + used, buf, take); used += take;
        if (used >= cap) break;
    }
    CLSOCK(s);
    out[used] = '\0';
    return (int)used;
}

/* ── jsmn helpers ──────────────────────────────────────────────────────── */
static int js_eq(const char* js, jsmntok_t* t, const char* s) {
    return (int)strlen(s) == t->end - t->start &&
           strncmp(js + t->start, s, (size_t)(t->end - t->start)) == 0;
}
static char* js_str(const char* js, jsmntok_t* t, char* out, size_t cap) {
    size_t n = (size_t)(t->end - t->start);
    if (n >= cap) n = cap - 1;
    memcpy(out, js + t->start, n); out[n] = '\0';
    return out;
}
/* jsmn tokens are FLAT; a value subtree consumes js_span tokens */
static int js_span(const jsmntok_t* t, int i) {
    int n = 1;
    if (t[i].type == JSMN_OBJECT) {
        int j;
        for (j = 0; j < t[i].size; j++)   /* each pair: key (1) + value */
            n += 1 + js_span(t, i + n + 1);
    } else if (t[i].type == JSMN_ARRAY) {
        int j;
        for (j = 0; j < t[i].size; j++) n += js_span(t, i + n);
    }
    return n;
}
/* find the VALUE token for `key` inside object token `obj` */
static jsmntok_t* js_get(const char* js, jsmntok_t* obj, const char* key) {
    int i;
    jsmntok_t* k = obj + 1;
    for (i = 0; i < obj->size; i++) {
        jsmntok_t* val = k + 1;
        if (js_eq(js, k, key)) return val;
        k = val + js_span(val, 0);
    }
    return NULL;
}
/* extract all string values in an array of strings into a buffer */
static void js_array_strs(const char* js, jsmntok_t* arr, char* out, size_t cap) {
    int i;
    jsmntok_t* t = arr + 1;
    size_t used = 0;
    for (i = 0; i < arr->size; i++) {
        char tmp[256];
        if (t->type == JSMN_OBJECT) {
            jsmntok_t* nm = js_get(js, t, "name");
            if (!nm || nm->type != JSMN_STRING) { t += js_span(t, 0); continue; }
            js_str(js, nm, tmp, sizeof(tmp));
        } else if (t->type == JSMN_STRING) {
            js_str(js, t, tmp, sizeof(tmp));
        } else { t += js_span(t, 0); continue; }
        size_t n = strlen(tmp);
        if (used + n + 2 < cap) {
            memcpy(out + used, tmp, n);
            used += n;
            out[used++] = ',';
        }
        t += js_span(t, 0);
    }
    if (used > 0) used--;
    out[used] = '\0';
}

/* ── registry + alerts ──────────────────────────────────────────────────── */
static void reg_record(const char* target, const char* value) {
    /* mesh registry targets: reg/STRAT/<sig>/<strat> (or colons) and
     * reg/MODEL/<qsig>/<model> — same keys the Python registry uses */
    char key[96];
    if (strncmp(target, "reg/POOL/", 9) == 0) {
        /* bridged learning is source-tagged (multi-homing):
         * reg/POOL/<pid>/STRAT/<sig>/<strat> -> POOL:<pid>:STRAT:<sig>:<strat> */
        const char* rest = target + 9;
        const char* slash = strchr(rest, '/');
        if (!slash) return;
        size_t pn = (size_t)(slash - rest);
        if (pn >= 72) return;
        snprintf(key, sizeof(key), "POOL:%.*s:%s", (int)pn, rest, slash + 1);
        for (char* c = key; *c; c++) if (*c == '/') *c = ':';
    } else {
        /* local-partition learning: keep the STRAT:/MODEL: family so
         * keys match the Python registry */
        const char* prefix = NULL;
        if (strncmp(target, "reg/STRAT", 9) == 0) prefix = target + 4;
        else if (strncmp(target, "reg/MODEL", 9) == 0) prefix = target + 4;
        if (!prefix) return;
        size_t n = strlen(prefix);
        if (n >= sizeof(key)) n = sizeof(key) - 1;
        memcpy(key, prefix, n); key[n] = '\0';
        for (size_t i = 0; i < n; i++) if (key[i] == '/') key[i] = ':';
    }
    int ok = (strcmp(value, "1") == 0 || strcmp(value, "1.0") == 0 ||
              strcmp(value, "true") == 0 || strcmp(value, "True") == 0);
    MUTEX_LOCK(&N.lock);
    int i; reg_entry_t* e = NULL;
    for (i = 0; i < N.reg_n; i++)
        if (strcmp(N.reg[i].key, key) == 0) { e = &N.reg[i]; break; }
    if (!e && N.reg_n < 256) { e = &N.reg[N.reg_n++]; memset(e, 0, sizeof(*e)); strncpy(e->key, key, sizeof(e->key)-1); }
    if (e) { e->total++; if (ok) e->ok++; }
    MUTEX_UNLOCK(&N.lock);
}

typedef struct { time_t ts; int level; char text[160]; } alert_t;
static alert_t alerts[32]; static int alerts_n, alerts_head;
static void alert_add(int level, const char* fmt, ...) {
    alert_t* a = &alerts[alerts_head];
    a->ts = time(NULL); a->level = level;
    va_list ap; va_start(ap, fmt);
    vsnprintf(a->text, sizeof(a->text), fmt, ap);
    va_end(ap);
    alerts_head = (alerts_head + 1) % 32;
    if (alerts_n < 32) alerts_n++;
}

/* ── history ring + sends ──────────────────────────────────────────────── */
static void hist_add(const char* line) {
    if (N.hist_n < 512) {
        N.hist[N.hist_n++] = strdup(line);
    } else {
        free(N.hist[N.hist_head]);
        N.hist[N.hist_head] = strdup(line);
        N.hist_head = (N.hist_head + 1) % 512;
    }
}
static void raw_send(sock_t s, const char* data, size_t n) {
    char* buf = malloc(n + 1);
    if (!buf) return;
    memcpy(buf, data, n); buf[n] = '\n';
    send(s, buf, (int)(n + 1), 0);
    free(buf);
}
static void raw_broadcast(const char* data, size_t n, sock_t except) {
    MUTEX_LOCK(&N.lock);
    for (conn_t* c = N.conns; c; c = c->next)
        if (c->s != except) raw_send(c->s, data, n);
    MUTEX_UNLOCK(&N.lock);
}
/* strip insignificant whitespace outside strings (model output is
 * pretty-printed; line-JSON framing requires a single-line plan) */
static void json_compact(const char* in, char* out, size_t cap) {
    size_t o = 0;
    int in_str = 0;
    for (const char* q = in; *q && o + 1 < cap; q++) {
        char ch = *q;
        if (in_str) {
            out[o++] = ch;
            if (ch == '\\' && q[1]) out[o++] = *++q;
            else if (ch == '"') in_str = 0;
        } else if (ch == '"') {
            in_str = 1;
            out[o++] = ch;
        } else if (ch != ' ' && ch != '\n' && ch != '\r' && ch != '\t') {
            out[o++] = ch;
        }
    }
    out[o] = '\0';
}

static void fmt_broadcast(const char* fmt, ...) {
    char buf[8192];
    va_list ap; va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > 0) raw_broadcast(buf, (size_t)n, BAD_SOCK);
}

/* mesh send callback: local mutate -> broadcast + history */
static void on_mesh_send(void* ud, const char* json, size_t len) {
    (void)ud;
    N.ops_published++;
    hist_add(json);
    raw_broadcast(json, len, BAD_SOCK);
    /* local publishes are learning too: record in the registry */
    jsmn_parser jp; jsmntok_t toks[256];
    jsmn_init(&jp);
    int nt = jsmn_parse(&jp, json, len, toks, 256);
    if (nt > 0 && toks[0].type == JSMN_OBJECT) {
        jsmntok_t* tgt = js_get(json, &toks[0], "target");
        jsmntok_t* val = js_get(json, &toks[0], "value");
        if (tgt && tgt->type == JSMN_STRING && val) {
            char t[96], v[32];
            js_str(json, tgt, t, sizeof(t));
            js_str(json, val, v, sizeof(v));
            reg_record(t, v);
        }
    }
}

/* ── control messages ──────────────────────────────────────────────────── */
static void handle_control(conn_t* c, const char* js, jsmntok_t* t, int ntoks) {
    jsmntok_t* type = js_get(js, t, "type");
    if (!type) return;
    if (js_eq(js, type, "hello") && ntoks > 4) {
        jsmntok_t* name = js_get(js, t, "name");
        if (name) js_str(js, name, c->peer_name, sizeof(c->peer_name));
        return;
    }
    if (js_eq(js, type, "capability")) {
        jsmntok_t* nm = js_get(js, t, "name");
        jsmntok_t* models = js_get(js, t, "models");
        if (nm) {
            MUTEX_LOCK(&N.lock);
            int i; cap_t* cap = NULL;
            for (i = 0; i < N.caps_n; i++)
                if (strcmp(N.caps[i].name, "") == 0) { cap = &N.caps[i]; break; }
            if (!cap && N.caps_n < 32) cap = &N.caps[N.caps_n++];
            if (cap) {
                memset(cap, 0, sizeof(*cap));
                js_str(js, nm, cap->name, sizeof(cap->name));
                if (models && models->type == JSMN_ARRAY)
                    js_array_strs(js, models, cap->models, sizeof(cap->models));
                cap->ts = time(NULL);
            }
            MUTEX_UNLOCK(&N.lock);
        }
        return;
    }
    if (js_eq(js, type, "req")) {
        /* inference request: answer with the local Ollama if present */
        jsmntok_t* id = js_get(js, t, "id");
        jsmntok_t* q = js_get(js, t, "query");
        char rid[64] = "", query[4096] = "";
        if (id) js_str(js, id, rid, sizeof(rid));
        if (q) js_str(js, q, query, sizeof(query));
        if (rid[0] && query[0]) {
#ifdef SS_EMBED_INFER
            /* Embedded path: QUEUE the request.  The llama.cpp context is
             * created by the embed_loop thread and must be driven from that
             * same thread — calling it from a conn thread wedges decode. */
            MUTEX_LOCK(&N.embed_lock);
            if (N.embed) {
                snprintf(N.req_query, sizeof(N.req_query), "%s", query);
                snprintf(N.req_id, sizeof(N.req_id), "%s", rid);
                N.req_pending = 1;   /* last request wins; resp from embed_loop */
                MUTEX_UNLOCK(&N.embed_lock);
                return;
            }
            MUTEX_UNLOCK(&N.embed_lock);
#endif
            /* no embedded model: Ollama fallback (runs on this thread) */
            if (N.ollama_ok) {
                char* reply = ollama_plan(query);
                if (reply) {
                    char plan_flat[2048];
                    json_compact(reply, plan_flat, sizeof(plan_flat));
                    fmt_broadcast("{\"type\":\"resp\",\"id\":\"%s\",\"ok\":true,\"plan\":%s}",
                                  rid, plan_flat);
                    free(reply);
                } else {
                    char line[256];
                    snprintf(line, sizeof(line),
                             "{\"type\":\"resp\",\"id\":\"%s\",\"ok\":false,\"error\":\"ollama plan failed\"}",
                             rid);
                    raw_send(c->s, line, strlen(line));
                }
            } else {
                char line[256];
                snprintf(line, sizeof(line),
                         "{\"type\":\"resp\",\"id\":\"%s\",\"ok\":false,\"error\":\"no inference provider available\"}",
                         rid);
                raw_send(c->s, line, strlen(line));
            }
        }
        return;
    }
    if (js_eq(js, type, "join_ok")) {
        /* pool hub accepted our signed invite: adopt the pool context
         * and the member allowlist (ops now flow from every member). */
        jsmntok_t* pool = js_get(js, t, "pool");
        jsmntok_t* members = js_get(js, t, "members");
        int n = 0;
        MUTEX_LOCK(&N.lock);
        if (members && members->type == JSMN_ARRAY) {
            for (int i = 0; i < members->size; i++) n++;
            js_array_strs(js, members, N.allow, sizeof(N.allow));
        }
        if (pool) js_str(js, pool, N.pool_label, sizeof(N.pool_label));
        MUTEX_UNLOCK(&N.lock);
        alert_add(0, "joined pool '%s' (%d members)", N.pool_label, n);
        return;
    }
    if (js_eq(js, type, "join_denied")) {
        alert_add(2, "pool join denied by hub (bad/expired invite?)");
        return;
    }
    if (js_eq(js, type, "member_added")) {
        jsmntok_t* nm = js_get(js, t, "name");
        if (nm) {
            char who[64] = "";
            js_str(js, nm, who, sizeof(who));
            alert_add(0, "pool member joined: %s", who);
            /* keep the allowlist current so their ops are accepted */
            if (N.allow[0] && who[0]) {
                size_t al = strlen(N.allow);
                if (al + strlen(who) + 2 < sizeof(N.allow)) {
                    N.allow[al] = ','; N.allow[al + 1] = '\0';
                    strncat(N.allow, who, sizeof(N.allow) - al - 2);
                }
            }
        }
        return;
    }
}

/* ── ollama ────────────────────────────────────────────────────────────── */
static void json_escape(const char* in, char* out, size_t cap) {
    size_t o = 0;
    for (const char* p = in; *p && o + 6 < cap; p++) {
        if (*p == '"' || *p == '\\') { out[o++] = '\\'; out[o++] = *p; }
        else if (*p == '\n') { out[o++] = '\\'; out[o++] = 'n'; }
        else if (*p == '\t') { out[o++] = '\\'; out[o++] = 't'; }
        else if (*p == '\r') { out[o++] = '\\'; out[o++] = 'r'; }
        else out[o++] = *p;
    }
    out[o] = '\0';
}
static char* extract_json_object(const char* text) {
    const char* s = strchr(text, '{');
    if (!s) return NULL;
    int depth = 0, in_str = 0, esc = 0;
    for (const char* p = s; *p; p++) {
        if (in_str) {
            if (esc) esc = 0;
            else if (*p == '\\') esc = 1;
            else if (*p == '"') in_str = 0;
        } else {
            if (*p == '"') in_str = 1;
            else if (*p == '{') depth++;
            else if (*p == '}') { depth--; if (depth == 0) { size_t n = (size_t)(p - s + 1); char* out = malloc(n + 1); memcpy(out, s, n); out[n] = '\0'; return out; } }
        }
    }
    return NULL;
}
/* planner system prompt: shared by the Ollama and embedded providers */
static const char* SS_PLAN_SYS =
    "You are SwarmState's planner. The kernel executes batch ops locally; "
    "reply with ONLY a JSON object, no prose: "
    "{\"ops\":[{\"type\":\"GREP\",\"pattern\":\"...\",\"target\":\"\"}],\"strategy\":\"DELTA\"}. "
    "Op types: READ(path,line_start,line_end), WRITE(path,content), GREP(pattern,target), "
    "DIFF(path), STATUS, EXECUTE(command), AST_PARSE(path), AST_QUERY(path,pattern), "
    "SYMBOL_SUMMARY(path,pattern,target). Strategies: DELTA, TARGETED, FULL, SYMBOLIC.";

/* unescape a JSON string value (the assistant content is stored escaped);
 * returns a malloc'd plain-text copy or NULL. */
static char* json_unescape(const char* start, const char* end) {
    char* out = (char*)malloc((size_t)(end - start) + 1);
    if (!out) return NULL;
    size_t o = 0;
    for (const char* p = start; p < end && *p; p++) {
        if (*p == '\\' && p + 1 < end) {
            p++;
            switch (*p) {
            case 'n': out[o++] = '\n'; break;
            case 't': out[o++] = '\t'; break;
            case 'r': out[o++] = '\r'; break;
            case '"': out[o++] = '"';  break;
            case '\\': out[o++] = '\\'; break;
            case '/': out[o++] = '/';  break;
            default:  out[o++] = '\\'; out[o++] = *p; break;
            }
        } else out[o++] = *p;
    }
    out[o] = '\0';
    return out;
}

/* Ollama /api/chat response -> the assistant's JSON plan object.
 * The content field is an escaped string ("{\n  \"ops\"...") — find it,
 * unescape, then extract the first balanced JSON object. */
static char* ollama_extract_plan(const char* text) {
    const char* c = strstr(text, "\"content\":");
    if (!c) return NULL;
    c += 10;                         /* past the key and colon */
    while (*c == ' ' || *c == '\t') c++;
    if (*c != '"') return NULL;      /* value must be a string */
    c++;                             /* opening quote of the value */
    const char* e = c;
    int esc = 0;
    while (*e) {
        if (esc) esc = 0;
        else if (*e == '\\') esc = 1;
        else if (*e == '"') break;
        e++;
    }
    if (*e != '"') return NULL;
    char* plain = json_unescape(c, e);
    if (!plain) return NULL;
    char* plan = extract_json_object(plain);
    free(plain);
    return plan;
}

static char* ollama_plan(const char* query) {
    char qesc[8192], sesc[8192], body[20480], resp[131072];
    json_escape(query, qesc, sizeof(qesc));
    json_escape(SS_PLAN_SYS, sesc, sizeof(sesc));   /* contains literal quotes */
    snprintf(body, sizeof(body),
        "{\"model\":\"%s\",\"messages\":[{\"role\":\"system\",\"content\":\"%s\"},"
        "{\"role\":\"user\",\"content\":\"%s\"}],\"stream\":false,\"format\":\"json\"}",
        N.model, sesc, qesc);
    int n = http_request("POST", "/api/chat", body, resp, sizeof(resp) - 1, 150000);
    if (n <= 0) return NULL;
    char* bodyp = strstr(resp, "\r\n\r\n");
    if (bodyp) bodyp += 4; else bodyp = resp;
    return ollama_extract_plan(bodyp);
}

/* detect Ollama (thread: once at boot, then every 10 s) */
static void ollama_loop(void* arg) {
    (void)arg;
    for (;;) {
        char resp[65536];
        int n = http_request("GET", "/api/tags", NULL, resp, sizeof(resp) - 1, 2000);
        int ok = 0;
        char models[1024] = "";
        if (n > 0) {
            char* bodyp = strstr(resp, "\r\n\r\n");
            if (bodyp) bodyp += 4; else bodyp = resp;
            jsmn_parser jp; jsmntok_t toks[2048];
            jsmn_init(&jp);
            int nt = jsmn_parse(&jp, bodyp, strlen(bodyp), toks,
                                (int)(sizeof(toks)/sizeof(toks[0])));
            if (nt > 0 && toks[0].type == JSMN_OBJECT) {
                jsmntok_t* m = js_get(bodyp, &toks[0], "models");
                if (m && m->type == JSMN_ARRAY && m->size > 0) {
                    js_array_strs(bodyp, m, models, sizeof(models));
                    ok = 1;
                }
            }
        }
        MUTEX_LOCK(&N.lock);
        if (ok != N.ollama_ok || (ok && strcmp(models, N.ollama_models) != 0)) {
            N.ollama_ok = ok;
            strncpy(N.ollama_models, models, sizeof(N.ollama_models) - 1);
            N.ollama_ts = time(NULL);
            if (ok) alert_add(1, "ollama detected: %s", models);
            else alert_add(2, "ollama lost");
        }
        MUTEX_UNLOCK(&N.lock);
        ss_sleep_ms(10000);
    }
}

#ifdef SS_EMBED_INFER
/* ── embedded inference (llama.cpp) ────────────────────────────────────── */
/* find a GGUF: --embed path, then $SS_MODEL_PATH, then the smallest
 * *.gguf under ~/.local/share/swarmstate/models (spec v1 §5.1). */
static int embed_find_model(char* out, size_t cap) {
    if (N.embed_model[0]) { snprintf(out, cap, "%s", N.embed_model); return 1; }
    const char* envp = getenv("SS_MODEL_PATH");
    if (envp && envp[0]) { snprintf(out, cap, "%s", envp); return 1; }
#ifndef _WIN32
    const char* home = getenv("HOME");
    if (home) {
        char dir[1024];
        snprintf(dir, sizeof(dir), "%s/.local/share/swarmstate/models", home);
        DIR* d = opendir(dir);
        if (d) {
            struct dirent* e;
            char best[1150] = ""; long best_sz = 0x7fffffffL;
            while ((e = readdir(d)) != NULL) {
                const char* g = strstr(e->d_name, ".gguf");
                if (!g || g[5] != '\0') continue;   /* only *.gguf files */
                char fp[1200];
                snprintf(fp, sizeof(fp), "%s/%s", dir, e->d_name);
                struct stat st;
                if (stat(fp, &st) == 0 && st.st_size < best_sz) {
                    best_sz = (long)st.st_size;
                    snprintf(best, sizeof(best), "%s", fp);
                }
            }
            closedir(d);
            if (best[0]) { snprintf(out, cap, "%s", best); return 1; }
        }
    }
#endif
    return 0;
}

/* load the embedded model at boot; retry until it appears.  ALL embedded
 * generation runs on THIS thread: the llama.cpp context is created here
 * and decode must be driven from the same thread. */
static void embed_loop(void* arg) {
    (void)arg;
    int model_ticks = 0;
    for (;;) {
        if (N.embed == NULL && (model_ticks++ % 100) == 0) {
            char path[512];
            if (embed_find_model(path, sizeof(path))) {
                alert_add(0, "embed: loading %s", path);
                ss_infer_ctx* ctx = ss_infer_init(path, 1024, 0);
                if (ctx) {
                    MUTEX_LOCK(&N.embed_lock);
                    if (N.embed == NULL) N.embed = ctx;
                    else { ss_infer_free(ctx); ctx = NULL; }
                    MUTEX_UNLOCK(&N.embed_lock);
                }
                if (ctx) {
                    alert_add(1, "embed: %s", ss_infer_model_desc(ctx));
                    if (ss_infer_cache_prefix(ctx, SS_PLAN_SYS) == 0)
                        alert_add(0, "embed: plan-system prefix cached");
                } else alert_add(2, "embed: model load failed: %s", path);
            }
        }
        char q[4096] = "", rid[64] = "";
        int take = 0;
        MUTEX_LOCK(&N.embed_lock);
        if (N.embed && N.req_pending) {
            snprintf(q, sizeof(q), "%s", N.req_query);
            snprintf(rid, sizeof(rid), "%s", N.req_id);
            N.req_pending = 0;
            take = 1;
        }
        MUTEX_UNLOCK(&N.embed_lock);
        if (take) {
            fprintf(stderr, "embed: gen start req=%s\n", rid);
            char* reply = ss_infer_generate(N.embed, SS_PLAN_SYS, q,
                                            ss_infer_plan_gbnf(), 160, 0.0f, NULL);
            fprintf(stderr, "embed: gen done: %s\n", reply ? "OK" : "NULL");
            if (reply) {
                char plan_flat[2048];
                json_compact(reply, plan_flat, sizeof(plan_flat));
                fmt_broadcast("{\"type\":\"resp\",\"id\":\"%s\",\"ok\":true,\"plan\":%s}",
                              rid, plan_flat);
                fprintf(stderr, "embed: resp broadcast done\n");
                free(reply);
            } else {
                char line[256];
                snprintf(line, sizeof(line),
                         "{\"type\":\"resp\",\"id\":\"%s\",\"ok\":false,\"error\":\"embedded inference failed\"}",
                         rid);
                raw_broadcast(line, strlen(line), BAD_SOCK);
                fprintf(stderr, "embed: error resp sent\n");
            }
        }
        ss_sleep_ms(50);
    }
}
#endif

/* ── connection handling ───────────────────────────────────────────────── */
static int allow_ok(const char* origin) {
    if (!N.allow[0]) return 1;   /* no allowlist: open mesh (v1 demo) */
    const char* p = N.allow;
    while (*p) {
        while (*p == ',' || *p == ' ') p++;
        const char* e = strchr(p, ',');
        size_t n = e ? (size_t)(e - p) : strlen(p);
        if (strlen(origin) == n && strncmp(origin, p, n) == 0) return 1;
        if (!e) break;
        p = e + 1;
    }
    return 0;
}
static void handle_line(conn_t* c, const char* line, size_t len) {
    jsmn_parser jp; jsmntok_t toks[1024];
    jsmn_init(&jp);
    int nt = jsmn_parse(&jp, line, len, toks, (int)(sizeof(toks)/sizeof(toks[0])));
    if (nt <= 0) return;
    if (toks[0].type == JSMN_OBJECT) {
        jsmntok_t* type = js_get(line, &toks[0], "type");
        if (type) { handle_control(c, line, &toks[0], nt); return; }
    }
    if (toks[0].type != JSMN_OBJECT) return;
    /* op: trust boundary — allowlist check on clock.origin */
    jsmntok_t* clk = js_get(line, &toks[0], "clock");
    char origin[64] = "";
    if (clk && clk->type == JSMN_OBJECT) {
        jsmntok_t* o = js_get(line, clk, "origin");
        if (o && o->type == JSMN_STRING) js_str(line, o, origin, sizeof(origin));
    }
    if (!allow_ok(origin)) {
        N.ops_dropped++;
        alert_add(2, "op dropped: origin '%s' not in allowlist", origin);
        return;
    }
    int accepted = mesh_peer_apply_json(N.peer, line, len);
    if (accepted) {
        N.ops_applied++;
        hist_add(line);
        raw_broadcast(line, len, c->s);   /* relay */
        /* registry learning */
        jsmntok_t* tgt = js_get(line, &toks[0], "target");
        jsmntok_t* val = js_get(line, &toks[0], "value");
        if (tgt && tgt->type == JSMN_STRING && val) {
            char t[96], v[32];
            js_str(line, tgt, t, sizeof(t));
            js_str(line, val, v, sizeof(v));
            reg_record(t, v);
        }
    }
}
static void conn_loop(void* arg) {
    conn_t* c = (conn_t*)arg;
#ifdef _WIN32
    DWORD tv = 500;
#else
    struct timeval tv; tv.tv_sec = 0; tv.tv_usec = 500000;
#endif
    setsockopt(c->s, SOL_SOCKET, SO_RCVTIMEO, (const char*)&tv, sizeof(tv));
    char buf[8192];
    for (;;) {
        int n = recv(c->s, buf, sizeof(buf), 0);
        if (n < 0) {
            /* idle timeout (SO_RCVTIMEO): keep the link alive, never
             * treat "no data yet" as a disconnect */
#ifdef _WIN32
            int e = WSAGetLastError();
            if (e == WSAETIMEDOUT || e == WSAEWOULDBLOCK) continue;
#else
            if (errno == EAGAIN || errno == EWOULDBLOCK) continue;
#endif
            break;
        }
        if (n == 0) break;
        if (c->rused + (size_t)n >= sizeof(c->rbuf)) { c->rused = 0; }
        memcpy(c->rbuf + c->rused, buf, (size_t)n);
        c->rused += (size_t)n;
        /* process complete lines */
        size_t start = 0;
        for (size_t i = 0; i < c->rused; i++) {
            if (c->rbuf[i] == '\n') {
                if (!c->caught_up) {
                    /* first inbound: catch-up (idempotent re-send) */
                    MUTEX_LOCK(&N.lock);
                    size_t h = N.hist_n < 512 ? 0 : N.hist_head;
                    for (size_t k = 0; k < N.hist_n; k++) {
                        size_t idx = (h + k) % 512;
                        raw_send(c->s, N.hist[idx], strlen(N.hist[idx]));
                    }
                    MUTEX_UNLOCK(&N.lock);
                    c->caught_up = 1;
                }
                if (i > start) handle_line(c, c->rbuf + start, i - start);
                start = i + 1;
            }
        }
        if (start > 0) {
            memmove(c->rbuf, c->rbuf + start, c->rused - start);
            c->rused -= start;
        }
    }
    /* disconnect */
    MUTEX_LOCK(&N.lock);
    conn_t** pp = &N.conns;
    while (*pp && *pp != c) pp = &(*pp)->next;
    if (*pp) *pp = c->next;
    MUTEX_UNLOCK(&N.lock);
    if (c->peer_name[0]) alert_add(2, "peer disconnected: %s", c->peer_name);
    CLSOCK(c->s);
    free(c);
}
static void conn_add(sock_t s, int outbound) {
    conn_t* c = (conn_t*)calloc(1, sizeof(conn_t));
    c->s = s;
    MUTEX_LOCK(&N.lock);
    c->next = N.conns;
    N.conns = c;
    MUTEX_UNLOCK(&N.lock);
    /* hello handshake: announce our node id (directed sends) */
    char line[128];
    snprintf(line, sizeof(line), "{\"type\":\"hello\",\"name\":\"%s\"}", N.name);
    raw_send(s, line, strlen(line));
    /* pool join: present the signed invite to the hub we dialed. */
    if (outbound && N.join_invite[0]) {
        char esc[8192];
        json_escape(N.join_invite, esc, sizeof(esc));
        char jl[8192 + 96];
        snprintf(jl, sizeof(jl),
                 "{\"type\":\"join\",\"invite\":\"%s\",\"name\":\"%s\"}",
                 esc, N.name);
        raw_send(s, jl, strlen(jl));
        N.join_sent = 1;
        alert_add(1, "presented pool invite to peer");
    }
    thr_spawn(conn_loop, c);
}
static void accept_loop(void* arg) {
    sock_t ls = (sock_t)(intptr_t)arg;
    for (;;) {
        struct sockaddr_in a; socklen_t al = sizeof(a);
        sock_t s = accept(ls, (struct sockaddr*)&a, &al);
        if (s == BAD_SOCK) { ss_sleep_ms(100); continue; }
        conn_add(s, 0);
    }
}
static void outbound_loop(void* arg) {
    (void)arg;
    for (;;) {
        for (int i = 0; i < N.out_n; i++) {
            int already = 0;
            MUTEX_LOCK(&N.lock);
            for (conn_t* c = N.conns; c; c = c->next) {
                struct sockaddr_in a; socklen_t al = sizeof(a);
                if (getpeername(c->s, (struct sockaddr*)&a, &al) == 0 &&
                    ntohs(a.sin_port) == (unsigned short)N.out[i].port)
                    already = 1;
            }
            MUTEX_UNLOCK(&N.lock);
            if (already) continue;
            sock_t s = socket(AF_INET, SOCK_STREAM, 0);
            if (s == BAD_SOCK) continue;
            struct sockaddr_in a; memset(&a, 0, sizeof(a));
            a.sin_family = AF_INET; a.sin_port = htons((unsigned short)N.out[i].port);
            a.sin_addr.s_addr = inet_addr(N.out[i].host);
            if (connect(s, (struct sockaddr*)&a, sizeof(a)) == 0) {
                conn_add(s, 1);
                alert_add(1, "connected to peer %s:%d", N.out[i].host, N.out[i].port);
            } else CLSOCK(s);
        }
        ss_sleep_ms(3000);
    }
}
static void announce_loop(void* arg) {
    (void)arg;
    for (;;) {
        if (N.ollama_ok) {
            char line[1024];
            snprintf(line, sizeof(line),
                     "{\"type\":\"capability\",\"name\":\"%s\",\"models\":[\"%s\"],\"load\":0.0,\"ts\":%lld}",
                     N.name, N.ollama_models, (long long)time(NULL));
            /* models are comma-separated; make it a proper JSON array */
            size_t n = strlen(line);
            raw_broadcast(line, n, BAD_SOCK);
        }
        ss_sleep_ms(5000);
    }
}
static void hash_loop(void* arg) {
    (void)arg;
    for (;;) {
        char hx[80]; state_hash_hex(hx, sizeof(hx));
        size_t tail = 0, comp = 0, rss = 0;
        mesh_peer_stats(N.peer, &tail, &comp, &rss);
        printf("HASH %s  tail=%zu rss_kb=%zu\n", hx, tail, rss);
        fflush(stdout);
        ss_sleep_ms((int)(N.hash_interval * 1000.0));
    }
}

/* ── web UI (spec: UI Kit v1 — shell + widgets + roles + config) ──────── */
#ifdef __has_include
#  if __has_include("qrcodegen_data.h")
#    include "qrcodegen_data.h"
#  endif
#endif
#ifndef QR_LIB_LEN
#define QR_LIB_LEN 0u
#define qrlib_js ""
#endif
static const char* UI_HTML = 
"<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
"<title>SwarmState node</title><style>"
"body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:16px}"
"h1{font-size:18px;margin:0 0 4px}h2{font-size:13px;margin:0 0 8px;color:#8b949e;text-transform:uppercase;letter-spacing:.05em}"
".top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}"
".ctx{background:#161b22;border:1px solid #30363d;padding:4px 10px;border-radius:6px;font-size:13px}"
".ctx b{color:#58a6ff}.role{background:#161b22;border:1px solid #30363d;color:#e6edf3;border-radius:6px;padding:4px 8px;font-size:13px}"
".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}"
".w{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px}"
"table{width:100%;border-collapse:collapse;font-size:12.5px}th,td{text-align:left;padding:4px 6px;border-bottom:1px solid #21262d}"
"th{color:#8b949e;font-weight:600}.ok{color:#3fb950}.bad{color:#f85149}.warn{color:#d29922}"
".alert{display:flex;gap:8px;padding:6px 8px;border-radius:6px;margin-bottom:6px;font-size:13px;border:1px solid}"
".a2{background:#3d1d1d55;border-color:#f8514977}.a1{background:#3d2f1d55;border-color:#d2992277}.a0{background:#1d3d2455;border-color:#3fb95077}"
".pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;background:#21262d;margin:1px}"
"@media(max-width:700px){.grid{grid-template-columns:1fr}}"
"</style></head><body>"
"<div class='top'><h1>SwarmState <span id='nname'>…</span></h1>"
"<span class='ctx'>Viewing: <b id='fctx'>standalone mesh</b></span>"
"<select class='role' id='fctxsel' onchange='setFctx(this.value)' title='federation context'></select>"
"<select class='role' id='role' onchange='switchRole(this.value)'></select>"
"<span class='ctx' id='kern'></span></div>"
"<div id='widgets' class='grid'></div>"
"<script src='/qr.js'></script>"
"<script>"
"const W={"
"status:{t:'Status',r:st=>`<table><tr><th>kernel</th><td>${st.kernel}</td></tr>"
"<tr><th>uptime</th><td>${fmt(st.uptime)}</td></tr>"
"<tr><th>mesh hash</th><td class='ok'>${st.hash.slice(0,16)}…</td></tr>"
"<tr><th>tail ops</th><td>${st.tail}</td></tr>"
"<tr><th>ops</th><td>${st.ops.published} pub / ${st.ops.applied} app / <span class='bad'>${st.ops.dropped} drop</span></td></tr>"
"<tr><th>disk</th><td>${st.disk.total? (st.disk.free/1e9).toFixed(1)+'/'+(st.disk.total/1e9).toFixed(1)+' GB free':'—'}</td></tr>"
"<tr><th>peers</th><td>${st.peers.length? st.peers.map(p=>`<span class='pill'>${p}</span>`).join(''):'<span class=\\'warn\\'>none</span>'}</td></tr></table>`},"
"tasks:{t:'Task list (local partition)',r:st=>st.registry.strat.length?`<table><tr><th>signature</th><th>strategy</th><th>rate</th></tr>"
"${st.registry.strat.map(e=>`<tr><td>${e.key.split(':').slice(0,2).join(':')}</td><td>${e.key.split(':')[2]||''}</td>"
"<td>${(100*e.ok/e.total).toFixed(0)}% <span class='${e.ok/e.total>=0.5?'ok':'bad'}'>(${e.ok}/${e.total})</span></td></tr>`).join('')}</table>`:'<span class=\\'warn\\'>no learned tasks yet</span>'},"
"fed:{t:'Federation (cross-pool learning)',r:st=>{const pools=(st.federation&&st.federation.pools)||[];"
"if(!pools.length)return '<span class=\\'warn\\'>no foreign-pool learning yet — bridge relays tag learning with its source pool</span>';"
"const show=FCTX==='all'?pools:pools.filter(p=>p.id===FCTX);"
"if(!show.length)return '<span class=\\'warn\\'>no learning for this pool yet</span>';"
"return show.map(p=>`<h3 class='pool'>${esc(p.id)} <span style='font-weight:400;color:#8b949e;font-size:12px'>foreign partition</span></h3>"
"${p.strat.length?`<table><tr><th>signature</th><th>strategy</th><th>rate</th></tr>"
"${p.strat.map(e=>`<tr><td>${e.key.split(':').slice(0,2).join(':')}</td><td>${e.key.split(':')[2]||''}</td>"
"<td>${(100*e.ok/e.total).toFixed(0)}% <span class='${e.ok/e.total>=0.5?'ok':'bad'}'>(${e.ok}/${e.total})</span></td></tr>`).join('')}</table>`:'<span class=\\'warn\\'>no strategy learning</span>'}"
"${p.model.length?`<table><tr><th>model</th><th>rate</th></tr>"
"${p.model.map(e=>`<tr><td>${esc(e.key)}</td><td>${(100*e.ok/e.total).toFixed(0)}% <span class='${e.ok/e.total>=0.5?'ok':'bad'}'>(${e.ok}/${e.total})</span></td></tr>`).join('')}</table>`:'<span class=\\'warn\\'>no model learning</span>'}`).join('')}},"
"alerts:{t:'Alerts',r:st=>st.alerts.length? st.alerts.slice(0,8).map(a=>`<div class='alert a${a.level}'>${time(a.ts)} ${esc(a.text)}</div>`).join(''):'<span class=\\'ok\\'>all quiet</span>'},"
"roster:{t:'Roster (capabilities)',r:st=>st.caps.length?`<table><tr><th>node</th><th>models</th><th>seen</th></tr>"
"${st.caps.map(c=>`<tr><td>${esc(c.name)}</td><td>${c.models.split(',').map(m=>`<span class='pill'>${esc(m)}</span>`).join('')}</td><td>${ago(c.ts)}</td></tr>`).join('')}</table>`:'<span class=\\'warn\\'>no capability announcements yet</span>'},"
"gov:{t:'Governance',r:st=>`<p style='font-size:13px'>High-risk ops (EXECUTE) require a signed token + reason — enforced server-side. "
"Approvals are logged in the audit ledger.</p><p style='font-size:13px'>Denials seen by this node: <b class='bad'>${st.ops.dropped}</b></p>"
"<p style='font-size:12px;color:#8b949e'>UI approval flows land in the governance layer of the Python harness; this node logs and surfaces them.</p>`},"
"qr:{t:'QR / invite',r:st=>{const inv=(st.join&&st.join.length>12),payload=inv?st.join:('--peer '+location.hostname+':'+st.port+' --allow '+st.name);"
"const qr=qrcode(0,'M');qr.addData(payload);qr.make();const svg=qr.createSvgTag(inv?3:4,0);"
"return `<p style='font-size:13px;color:#8b949e'>${inv?'Pool invite - scan to join this pool':'Join line - point another node at this one'}</p>`+"
"`<div style='background:#fff;display:inline-block;padding:6px;border-radius:6px;margin:4px 0 8px'>${svg}</div>`+"
"`<pre style='background:#0d1117;padding:8px;border-radius:6px;font-size:11px;word-break:break-all'>${esc(payload)}</pre>`+"
"`<button onclick='navigator.clipboard.writeText(this.dataset.c)' data-c='${esc(payload)}'>copy</button>`}}"
"};"
"const ROLES={field_worker:['alerts','tasks','status'],supervisor:['status','alerts','roster','gov','tasks','fed','qr'],"
"admin:['status','roster','gov','qr','tasks','fed','alerts'],observer:['status','alerts']};"
"let CUR='supervisor',CFG=null,ST=null,FCTX='all';"
"function setFctx(v){FCTX=v;document.getElementById('fctx').textContent=(v==='all'?'all pools':v+' (foreign)');draw()}"
"function fmt(s){s=+s;const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return d?d+'d '+h+'h':h?h+'h '+m+'m':m+'m'}"
"function time(ts){return new Date(ts*1000).toLocaleTimeString()}function ago(ts){return Math.floor(Date.now()/1000-ts)+'s ago'}"
"function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}"
"function switchRole(r){CUR=r;location.hash=r;draw()}function role(){return location.hash.slice(1)||'supervisor'}"
"function draw(){const list=(CFG&&CFG.roles&&CFG.roles[CUR])||ROLES[CUR]||ROLES.supervisor;"
"document.getElementById('widgets').innerHTML=list.map(w=>`<section class='w'><h2>${W[w]?W[w].t:w}</h2><div class='b'>${W[w]?W[w].r(ST||{}):'widget unavailable'}</div></section>`).join('')}"
"function poll(){fetch('/api/state').then(r=>r.json()).then(st=>{ST=st;"
"document.getElementById('nname').textContent=st.name;document.getElementById('fctx').textContent=st.context||'standalone mesh';"
"document.getElementById('kern').textContent=st.kernel;"
"const pools=(st.federation&&st.federation.pools)||[];const sel=document.getElementById('fctxsel');"
"sel.innerHTML=['all'].concat(pools.map(p=>p.id)).map(p=>`<option value='${p}' ${p===FCTX?'selected':''}>${p==='all'?'all pools':p}</option>`).join('');"
"draw()}).catch(()=>{})}"
"fetch('/api/config').then(r=>r.json()).then(c=>{CFG=c;const rs=Object.keys((c.roles)||{});"
"const sel=document.getElementById('role');sel.innerHTML=rs.map(r=>`<option value='${r}' ${r===role()?'selected':''}>${r}</option>`).join('');"
"CUR=role();draw();setInterval(poll,2000);poll();});"
"</script></body></html>";

static void web_conn_loop(void* arg) {
    sock_t s = (sock_t)(intptr_t)arg;
    char req[8192]; size_t used = 0;
#ifdef _WIN32
    DWORD tv = 3000;
#else
    struct timeval tv; tv.tv_sec = 3; tv.tv_usec = 0;
#endif
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char*)&tv, sizeof(tv));
    int n;
    while (used < sizeof(req) - 1 && (n = recv(s, req + used, 256, 0)) > 0) {
        used += (size_t)n;
        if (ss_memmem(req, used, "\r\n\r\n", 4)) break;
    }
    req[used] = '\0';
    char path[512] = "/";
    if (strncmp(req, "GET ", 4) == 0) {
        char* sp = strchr(req + 4, ' ');
        if (sp) { size_t pl = (size_t)(sp - (req + 4)); if (pl >= sizeof(path)) pl = sizeof(path) - 1; memcpy(path, req + 4, pl); path[pl] = '\0'; }
    }
    char body[262144]; size_t blen = 0; const char* ctype = "text/html; charset=utf-8";
    if (strcmp(path, "/api/state") == 0) { api_state(body, sizeof(body)); blen = strlen(body); ctype = "application/json"; }
    else if (strcmp(path, "/qr.js") == 0) {
        size_t ql = (size_t)QR_LIB_LEN;
        if (ql >= sizeof(body)) ql = sizeof(body) - 1;
        memcpy(body, qrlib_js, ql); body[ql] = '\0';
        blen = ql; ctype = "application/javascript";
    }
    else if (strcmp(path, "/api/config") == 0) { snprintf(body, sizeof(body), "%s", N.config_json[0] ? N.config_json : "{}"); blen = strlen(body); ctype = "application/json"; }
    else { snprintf(body, sizeof(body), "%s", UI_HTML); blen = strlen(body); }
    char hdr[256];
    int hn = snprintf(hdr, sizeof(hdr),
        "HTTP/1.0 200 OK\r\nContent-Type: %s\r\nContent-Length: %zu\r\n"
        "Cache-Control: no-store\r\nConnection: close\r\n\r\n", ctype, blen);
    send(s, hdr, hn, 0);
    size_t off = 0;
    while (off < blen) {
        int chunk = (int)(blen - off); if (chunk > 32768) chunk = 32768;
        int w = send(s, body + off, chunk, 0);
        if (w <= 0) break;
        off += (size_t)w;
    }
    CLSOCK(s);
}
static void web_accept_loop(void* arg) {
    sock_t ls = (sock_t)(intptr_t)arg;
    for (;;) {
        struct sockaddr_in a; socklen_t al = sizeof(a);
        sock_t s = accept(ls, (struct sockaddr*)&a, &al);
        if (s == BAD_SOCK) { ss_sleep_ms(100); continue; }
        thr_spawn(web_conn_loop, (void*)(intptr_t)s);
    }
}
static void api_state(char* out, size_t cap) {
    size_t u = 0;
#define AP(...) do { int _w = snprintf(out + u, cap - u, __VA_ARGS__); if (_w > 0) u += (size_t)_w; if (u >= cap) return; } while (0)
    char hx[80]; state_hash_hex(hx, sizeof(hx));
    size_t tail = 0, comp = 0, rss = 0;
    mesh_peer_stats(N.peer, &tail, &comp, &rss);
    AP("{\"name\":\"%s\",\"port\":%d,\"uptime\":%lld,\"web_port\":%d,", N.name, N.port,
       (long long)(time(NULL) - N.started), N.web_port);
    AP("\"hash\":\"%s\",\"tail\":%zu,\"compacted\":%zu,\"rss_kb\":%zu,", hx, tail, comp, rss);
    AP("\"ops\":{\"published\":%zu,\"applied\":%zu,\"dropped\":%zu},", N.ops_published, N.ops_applied, N.ops_dropped);
    AP("\"kernel\":\"%s\",", N.kernel_ver);
    AP("\"disk\":{\"total\":%llu,\"free\":%llu},", (unsigned long long)N.disk_total, (unsigned long long)N.disk_free);
    AP("\"context\":\"%s\",", N.pool_label);
    AP("\"join\":\"%s\",", N.join_invite);
    MUTEX_LOCK(&N.lock);
    AP("\"peers\":[");
    int first = 1;
    for (conn_t* c = N.conns; c; c = c->next) {
        AP("%s\"%s\"", first ? "" : ",", c->peer_name[0] ? c->peer_name : "?");
        first = 0;
    }
    AP("],\"ollama\":{\"ok\":%d,\"models\":[", N.ollama_ok);
    {
        char* p = N.ollama_models; first = 1;
        while (*p) {
            char* e = strchr(p, ',');
            if (e) *e = '\0';
            AP("%s\"%s\"", first ? "" : ",", p);
            first = 0;
            if (!e) break;
            p = e + 1;
        }
    }
#ifdef SS_EMBED_INFER
    AP("]},\"embed\":{\"ok\":%d,\"model\":\"%s\"},",
       N.embed ? 1 : 0, N.embed ? ss_infer_model_desc(N.embed) : "");
#else
    AP("]},\"embed\":{\"ok\":false},");
#endif
    AP("\"registry\":{\"strat\":[");
    /* LOCAL partition only: the keys local routers read.  Foreign
     * (POOL:<pid>:...) learning lives under federation.pools. */
    first = 1;
    for (int i = 0; i < N.reg_n; i++) {
        if (strncmp(N.reg[i].key, "STRAT:", 6) != 0) continue;
        AP("%s{\"key\":\"%s\",\"ok\":%d,\"total\":%d}", first ? "" : ",", N.reg[i].key, N.reg[i].ok, N.reg[i].total);
        first = 0;
    }
    AP("],\"model\":[");
    first = 1;
    for (int i = 0; i < N.reg_n; i++) {
        if (strncmp(N.reg[i].key, "MODEL:", 6) != 0) continue;
        AP("%s{\"key\":\"%s\",\"ok\":%d,\"total\":%d}", first ? "" : ",", N.reg[i].key, N.reg[i].ok, N.reg[i].total);
        first = 0;
    }
    AP("]},\"federation\":{\"pools\":[");
    /* per-pool partitions: POOL:<pid>:STRAT|MODEL:<rest> -> one entry
     * per foreign pool; keys shown without the POOL:<pid>: prefix. */
    first = 1;
    int npids = 0;
    {
        char pids[8][64];
        for (int i = 0; i < N.reg_n && npids < 8; i++) {
            const char* k = N.reg[i].key;
            if (strncmp(k, "POOL:", 5) != 0) continue;
            const char* c = strchr(k + 5, ':');
            if (!c) continue;
            size_t pl = (size_t)(c - (k + 5)); if (pl >= 63) pl = 63;
            int seen = 0;
            for (int j = 0; j < npids; j++)
                if (strncmp(pids[j], k + 5, pl) == 0 && pids[j][pl] == '\0') { seen = 1; break; }
            if (!seen) { memcpy(pids[npids], k + 5, pl); pids[npids][pl] = '\0'; npids++; }
        }
        for (int pi = 0; pi < npids; pi++) {
            char pre[80]; int f2;
            snprintf(pre, sizeof(pre), "POOL:%s:", pids[pi]);
            AP("%s{\"id\":\"%s\",\"strat\":[", first ? "" : ",", pids[pi]);
            f2 = 1;
            for (int i = 0; i < N.reg_n; i++) {
                const char* k = N.reg[i].key;
                if (strncmp(k, pre, strlen(pre)) != 0) continue;
                const char* rest = k + strlen(pre);
                if (strncmp(rest, "STRAT:", 6) != 0) continue;
                AP("%s{\"key\":\"%s\",\"ok\":%d,\"total\":%d}", f2 ? "" : ",", rest, N.reg[i].ok, N.reg[i].total);
                f2 = 0;
            }
            AP("],\"model\":[");
            f2 = 1;
            for (int i = 0; i < N.reg_n; i++) {
                const char* k = N.reg[i].key;
                if (strncmp(k, pre, strlen(pre)) != 0) continue;
                const char* rest = k + strlen(pre);
                if (strncmp(rest, "MODEL:", 6) != 0) continue;
                AP("%s{\"key\":\"%s\",\"ok\":%d,\"total\":%d}", f2 ? "" : ",", rest, N.reg[i].ok, N.reg[i].total);
                f2 = 0;
            }
            AP("]}");
            first = 0;
        }
    }
    AP("],\"count\":%d},\"caps\":[", npids);
    first = 1;
    for (int i = 0; i < N.caps_n; i++) {
        AP("%s{\"name\":\"%s\",\"models\":\"%s\",\"load\":%.2f,\"ts\":%lld}",
           first ? "" : ",", N.caps[i].name, N.caps[i].models, N.caps[i].load,
           (long long)N.caps[i].ts);
        first = 0;
    }
    AP("],\"alerts\":[");
    first = 1;
    for (int i = 0; i < alerts_n; i++) {
        int idx = (alerts_head - alerts_n + i + 32 * 2) % 32;
        alert_t* a = &alerts[idx];
        AP("%s{\"ts\":%lld,\"level\":%d,\"text\":\"%s\"}", first ? "" : ",",
           (long long)a->ts, a->level, a->text);
        first = 0;
    }
    AP("]");
    MUTEX_UNLOCK(&N.lock);
    AP("}");
#undef AP
}

/* ── config (UI kit: widget layout assembled from a file, not code) ───── */
static void load_config(const char* argv0) {
    char dir[1024] = ".";
#ifdef _WIN32
    GetModuleFileNameA(NULL, dir, sizeof(dir));
    char* slash = strrchr(dir, '\\');
    if (slash) *slash = '\0';
#else
    (void)argv0;
#endif
    char path[2048];
    snprintf(path, sizeof(path), "%s/config/widgets.json", dir);
    FILE* f = fopen(path, "rb");
    if (f) {
        size_t got = fread(N.config_json, 1, sizeof(N.config_json) - 1, f);
        N.config_json[got] = '\0';
        fclose(f);
        return;
    }
    /* default: the UI kit v1 role views */
    snprintf(N.config_json, sizeof(N.config_json),
        "{\"roles\":{"
        "\"field_worker\":[\"alerts\",\"tasks\",\"status\"],"
        "\"supervisor\":[\"status\",\"alerts\",\"roster\",\"gov\",\"tasks\",\"qr\"],"
        "\"admin\":[\"status\",\"roster\",\"gov\",\"qr\",\"tasks\",\"alerts\"],"
        "\"observer\":[\"status\",\"alerts\"]}}");
}

/* ── main ─────────────────────────────────────────────────────────────── */
static void usage(const char* prog) {
    printf("SwarmState node (self-contained; C mesh base + kernel dll + web UI)\\n\\n"
           "usage: %s --name ID [options]\\n"
           "  --port N          mesh listen port (default 7700)\\n"
           "  --peer HOST:PORT  outbound peer (repeatable)\\n"
           "  --allow a,b       peer ids allowed to inject ops (default: open)\\n"
           "  --join INVITE     signed pool invite to present to the hub\\n"
           "  --web N           local web UI port (default 8080; 0 = off)\\n"
           "  --publish 'T V'   publish a registry op at startup (repeatable)\\n"
           "  --demo            publish a small demo learning set\\n"
           "  --model M         ollama model to serve inference with\\n"
           "  --embed PATH      embedded GGUF model (llama.cpp; auto-scan if unset)\\n"
           "  --pool-name N     federation context label shown in the UI\\n"
           "  --root DIR        kernel repo root (kernel sysinfo)\\n"
           "  --hash-interval S hash print interval (default 5)\\n", prog);
}
static void demo_publish(void) {
    mesh_peer_mutate(N.peer, MESH_OP_APP, "reg/STRAT/WRITE:DELTA", "1");
    mesh_peer_mutate(N.peer, MESH_OP_APP, "reg/STRAT/WRITE:DELTA", "1");
    mesh_peer_mutate(N.peer, MESH_OP_APP, "reg/STRAT/WRITE:DELTA", "1");
    mesh_peer_mutate(N.peer, MESH_OP_APP, "reg/STRAT/WRITE:DELTA", "1");
    mesh_peer_mutate(N.peer, MESH_OP_APP, "reg/STRAT/WRITE:DELTA", "0");
    mesh_peer_mutate(N.peer, MESH_OP_APP, "reg/STRAT/WRITE:FULL", "1");
    mesh_peer_mutate(N.peer, MESH_OP_APP, "reg/STRAT/WRITE:FULL", "1");
    mesh_peer_mutate(N.peer, MESH_OP_APP, "reg/MODEL:q:rename:1.5b", "1");
    mesh_peer_mutate(N.peer, MESH_OP_APP, "reg/MODEL:q:rename:7b", "0");
}
int main(int argc, char** argv) {
    setvbuf(stdout, NULL, _IONBF, 0);  /* logs live: no block buffering */
#ifdef _WIN32
    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
#endif
    memset(&N, 0, sizeof(N));
    N.started = time(NULL);
    N.port = 7700;
    N.web_port = 8080;
    N.hash_interval = 5.0;
    snprintf(N.name, sizeof(N.name), "node-1");
    snprintf(N.model, sizeof(N.model), "qwen2.5-coder:1.5b");
    snprintf(N.pool_label, sizeof(N.pool_label), "standalone mesh");
    for (int i = 1; i < argc; i++) {
        const char* a = argv[i];
        #define NEXT() (i + 1 < argc ? argv[++i] : "")
        if (strcmp(a, "--name") == 0) snprintf(N.name, sizeof(N.name), "%s", NEXT());
        else if (strcmp(a, "--port") == 0) N.port = atoi(NEXT());
        else if (strcmp(a, "--web") == 0) N.web_port = atoi(NEXT());
        else if (strcmp(a, "--hash-interval") == 0) N.hash_interval = atof(NEXT());
        else if (strcmp(a, "--allow") == 0) snprintf(N.allow, sizeof(N.allow), "%s", NEXT());
        else if (strcmp(a, "--join") == 0) snprintf(N.join_invite, sizeof(N.join_invite), "%s", NEXT());
        else if (strcmp(a, "--pool-name") == 0) snprintf(N.pool_label, sizeof(N.pool_label), "%s", NEXT());
        else if (strcmp(a, "--root") == 0) snprintf(N.root, sizeof(N.root), "%s", NEXT());
        else if (strcmp(a, "--model") == 0) snprintf(N.model, sizeof(N.model), "%s", NEXT());
        else if (strcmp(a, "--embed") == 0) {
#ifdef SS_EMBED_INFER
            snprintf(N.embed_model, sizeof(N.embed_model), "%s", NEXT());
#else
            NEXT();
#endif
        }
        else if (strcmp(a, "--demo") == 0) N.demo = 1;
        else if (strcmp(a, "--peer") == 0) {
            const char* spec = NEXT();
            if (N.out_n < 16) {
                char host[128]; int port = 0;
                const char* colon = strrchr(spec, ':');
                if (colon) {
                    size_t hl = (size_t)(colon - spec);
                    if (hl >= sizeof(host)) hl = sizeof(host) - 1;
                    memcpy(host, spec, hl); host[hl] = '\0';
                    port = atoi(colon + 1);
                } else { snprintf(host, sizeof(host), "%s", spec); port = 7700; }
                snprintf(N.out[N.out_n].host, sizeof(N.out[N.out_n].host), "%s", host);
                N.out[N.out_n].port = port;
                N.out_n++;
            }
        }
        else if (strcmp(a, "--publish") == 0) {
            const char* pv = NEXT();
            char t[96], v[64];
            const char* sp = pv;
            while (*sp && *sp != ' ' && *sp != '\t') sp++;
            if (*sp) {
                size_t tl = (size_t)(sp - pv);
                if (tl >= sizeof(t)) tl = sizeof(t) - 1;
                memcpy(t, pv, tl); t[tl] = '\0';
                snprintf(v, sizeof(v), "%s", sp + 1);
            } else { snprintf(t, sizeof(t), "%s", pv); snprintf(v, sizeof(v), "1"); }
            if (N.pubs_n < 64) {
                snprintf(N.pubs[N.pubs_n].t, sizeof(N.pubs[N.pubs_n].t), "%s", t);
                snprintf(N.pubs[N.pubs_n].v, sizeof(N.pubs[N.pubs_n].v), "%s", v);
                N.pubs_n++;
            }
        }
        else if (strcmp(a, "-h") == 0 || strcmp(a, "--help") == 0) { usage(argv[0]); return 0; }
        #undef NEXT
    }
    /* kernel DLL */
    const char* kv = ss_version();
    if (kv) snprintf(N.kernel_ver, sizeof(N.kernel_ver), "%s", kv);
    else snprintf(N.kernel_ver, sizeof(N.kernel_ver), "kernel unavailable");
    alert_add(0, "kernel: %s", N.kernel_ver);
    if (N.root[0]) {
        RepoState* st = ss_state_new(N.root);
        if (st) {
            SS_SystemInfo si;
            memset(&si, 0, sizeof(si));
            if (ss_sysinfo(st, &si) == 0) {
                N.disk_total = si.disk_total_bytes;
                N.disk_free = si.disk_free_bytes;
            }
            ss_state_free(st);
        }
    }
    /* mesh peer (shared C base) */
    N.peer = mesh_peer_new(N.name, 12);
    mesh_peer_set_send(N.peer, on_mesh_send, NULL);
    MUTEX_INIT(&N.lock);
#ifdef SS_EMBED_INFER
    MUTEX_INIT(&N.embed_lock);
#endif
    load_config(argv[0]);

    /* mesh listener */
    sock_t ls = BAD_SOCK;
    if (N.port > 0) {
        ls = socket(AF_INET, SOCK_STREAM, 0);
        int one = 1;
        setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, (const char*)&one, sizeof(one));
        struct sockaddr_in a; memset(&a, 0, sizeof(a));
        a.sin_family = AF_INET; a.sin_port = htons((unsigned short)N.port);
        a.sin_addr.s_addr = htonl(INADDR_ANY);
        if (bind(ls, (struct sockaddr*)&a, sizeof(a)) == 0 && listen(ls, 8) == 0) {
            thr_spawn(accept_loop, (void*)(intptr_t)ls);
        } else { CLSOCK(ls); ls = BAD_SOCK; }
    }
    /* web UI */
    if (N.web_port > 0) {
        sock_t ws = socket(AF_INET, SOCK_STREAM, 0);
        int one = 1;
        setsockopt(ws, SOL_SOCKET, SO_REUSEADDR, (const char*)&one, sizeof(one));
        struct sockaddr_in a; memset(&a, 0, sizeof(a));
        a.sin_family = AF_INET; a.sin_port = htons((unsigned short)N.web_port);
        a.sin_addr.s_addr = htonl(INADDR_ANY);
        if (bind(ws, (struct sockaddr*)&a, sizeof(a)) == 0 && listen(ws, 8) == 0) {
            thr_spawn(web_accept_loop, (void*)(intptr_t)ws);
            alert_add(0, "web UI on http://127.0.0.1:%d", N.web_port);
        } else CLSOCK(ws);
    }
    if (!getenv("SS_NO_AUX_LOOPS")) {
        thr_spawn(ollama_loop, NULL);
        thr_spawn(announce_loop, NULL);
        thr_spawn(hash_loop, NULL);
        if (N.out_n > 0) thr_spawn(outbound_loop, NULL);
    } else {
        printf("aux loops disabled (SS_NO_AUX_LOOPS)\n");
    }
#ifdef SS_EMBED_INFER
    thr_spawn(embed_loop, NULL);
#endif

    for (int i = 0; i < N.pubs_n; i++)
        mesh_peer_mutate(N.peer, MESH_OP_APP, N.pubs[i].t, N.pubs[i].v);
    if (N.demo) demo_publish();
    printf("SwarmState node '%s' up: mesh :%d  web :%d  kernel=%s\n",
           N.name, N.port, N.web_port, N.kernel_ver);
    if (ls != BAD_SOCK)
        printf("  join: --peer 127.0.0.1:%d --allow %s\n", N.port, N.name);
    fflush(stdout);
    for (;;) ss_sleep_ms(1000);
    return 0;
}
