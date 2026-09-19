/* regex.h — POSIX-regex SUBSET for the mingw/Windows kernel build.
 * Literals, '.', '^'/'$', '*','+','?', '[...]'/'[^...]', groups '()',
 * alternation '|', backslash escapes (\d \w \s \b and escaped metas).
 * Unsupported constructs degrade to literals.  Compile-time shim only
 * (never built on Linux, where the real POSIX regex is used). */
#ifndef SP_REGEX_H
#define SP_REGEX_H
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { char* pat; size_t len; } regex_t;
typedef struct { int rm_so, rm_eo; } regmatch_t;
#define REG_EXTENDED 1
#define REG_NOSUB    2
#define REG_ICASE    4

static int regcomp(regex_t* r, const char* pat, int flags) {
    (void)flags;
    size_t n = strlen(pat);
    r->pat = (char*)malloc(n + 1);
    if (!r->pat) return 1;
    memcpy(r->pat, pat, n + 1);
    r->len = n;
    return 0;
}
static void regfree(regex_t* r) { free(r->pat); r->pat = NULL; r->len = 0; }
static void regerror(int err, const regex_t* r, char* buf, size_t n) {
    (void)err; (void)r;
    if (n > 0) { snprintf(buf, n, "regex compile error"); }
}

/* match pattern p[i..] against s[pos..]; returns new pos or -1 */
static int re_match(const char* p, int i, const char* s, int pos);
static int re_match_atom(const char* p, int i, const char* s, int pos, int* next_i);

static int re_is_class_char(char c, const char* cls, int len, int neg) {
    int in = 0, k = 0;
    while (k < len) {
        if (cls[k] == '\\' && k + 1 < len) { if (cls[k+1] == 'd') { if (c >= '0' && c <= '9') in = 1; }
            else if (cls[k+1] == 'w') { if ((c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_') in = 1; }
            else if (cls[k+1] == 's') { if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v') in = 1; }
            else if (cls[k+1] == c) in = 1;
            k += 2; continue; }
        if (k + 2 < len && cls[k+1] == '-' && cls[k+2] != ']') {
            if (cls[k] <= c && c <= cls[k+2]) in = 1;
            k += 3; continue;
        }
        if (cls[k] == c) in = 1;
        k++;
    }
    return neg ? !in : in;
}

/* returns new pos after matching atom at p[i] against s[pos], or -1 */
static int re_match_atom(const char* p, int i, const char* s, int pos, int* next_i) {
    char c = p[i];
    if (c == '\\') {
        char e = p[i+1];
        *next_i = i + 2;
        if (e == 'd') return (s[pos] >= '0' && s[pos] <= '9') ? pos + 1 : -1;
        if (e == 'w') return ((s[pos] >= '0' && s[pos] <= '9') || (s[pos] >= 'a' && s[pos] <= 'z') || (s[pos] >= 'A' && s[pos] <= 'Z') || s[pos] == '_') ? pos + 1 : -1;
        if (e == 's') return (s[pos] == ' ' || s[pos] == '\t' || s[pos] == '\n' || s[pos] == '\r') ? pos + 1 : -1;
        if (e == 'b') { *next_i = i + 2; return 0; }   /* \b: zero-width, treat as ok */
        return (s[pos] == e) ? pos + 1 : -1;
    }
    if (c == '.') { *next_i = i + 1; return s[pos] ? pos + 1 : -1; }
    if (c == '[') {
        int neg = 0, k = i + 1;
        if (p[k] == '^') { neg = 1; k++; }
        int start = k;
        while (p[k] && p[k] != ']') k++;
        if (!p[k]) { *next_i = i + 1; return (s[pos] == '[') ? pos + 1 : -1; }
        if (re_is_class_char(s[pos], p + start, k - start, neg)) { *next_i = k + 1; return pos + 1; }
        return -1;
    }
    if (c == '(') {
        /* find matching ')' respecting nesting */
        int depth = 1, k = i + 1, close = -1;
        while (p[k]) {
            if (p[k] == '\\') { k += 2; continue; }
            if (p[k] == '(') depth++;
            else if (p[k] == ')') { depth--; if (depth == 0) { close = k; break; } }
            k++;
        }
        if (close < 0) { *next_i = i + 1; return -1; }
        /* extract group content p[i+1 .. close-1] and match as alternation */
        char* g = (char*)malloc((size_t)(close - i) + 1);
        if (!g) { *next_i = close + 1; return -1; }
        memcpy(g, p + i + 1, (size_t)(close - i - 1));
        g[close - i - 1] = '\0';
        *next_i = close + 1;
        int r = re_match(g, 0, s, pos);
        free(g);
        return r;
    }
    if (c == '|' || c == ')' || c == '^' || c == '$') { *next_i = i + 1; return -1; }
    *next_i = i + 1;
    return (s[pos] == c) ? pos + 1 : -1;
}

/* match pattern p[i..] against s[pos..]: alternation at top level */
static int re_match_alt(const char* p, int i, const char* s, int pos) {
    /* find top-level '|' boundaries; try each alternative */
    int depth = 0, start = i, k = i;
    for (;;) {
        char c = p[k];
        if (c == '\0') { return re_match(p, start, s, pos); }
        if (c == '\\') { k += 2; continue; }
        if (c == '(') { depth++; k++; continue; }
        if (c == ')') { if (depth == 0) return re_match(p, start, s, pos); depth--; k++; continue; }
        if (c == '|' && depth == 0) {
            char* alt = (char*)malloc((size_t)(k - start) + 1);
            if (!alt) return -1;
            memcpy(alt, p + start, (size_t)(k - start));
            alt[k - start] = '\0';
            int r = re_match(alt, 0, s, pos);
            free(alt);
            if (r >= 0) return r;
            start = k + 1;
        }
        k++;
    }
}

/* match pattern p[i..] against s[pos..]: sequence of quantified atoms */
static int re_match(const char* p, int i, const char* s, int pos) {
    int anchor_start = (p[i] == '^');
    if (anchor_start) { i++; if (pos != 0) return -1; }
    for (;;) {
        char c = p[i];
        if (c == '\0' || c == '|' || (c == ')' && 1)) {
            /* end of this alternative: ')' handled by caller boundary */
            if (c == '\0') return pos;
            /* '|' or ')' reached at top: stop here (alt/match caller) */
            return pos;
        }
        if (c == '$') { i++; if (p[i] == '\0') return (s[pos] == '\0') ? pos : -1; continue; }
        int next_i = 0;
        int r = re_match_atom(p, i, s, pos, &next_i);
        /* quantifier? */
        char q = p[next_i];
        if (q == '*' || q == '+' || q == '?') {
            int min = (q == '+') ? 1 : 0;
            int max = (q == '?') ? 1 : 1000000000;
            /* try max..min matches (greedy) */
            /* first consume greedily via repeated atom match */
            int n = 0, cur = pos;
            while (n < max) {
                int r2 = re_match_atom(p, i, s, cur, &next_i);
                if (r2 < 0) break;
                cur = r2; n++;
            }
            while (n >= min) {
                /* try rest after n matches */
                int rest = re_match(p, next_i, s, cur);
                if (rest >= 0) return rest;
                if (n == 0) break;
                /* backtrack one match: need to un-consume -> recompute */
                n--;
                /* recompute cur by matching atom n times from pos */
                cur = pos; int t;
                for (t = 0; t < n; t++) { int ni2; int rr = re_match_atom(p, i, s, cur, &ni2); if (rr < 0) break; cur = rr; }
            }
            return -1;
        }
        if (r < 0) return -1;
        pos = r;
        i = next_i;
    }
}

static int regexec(const regex_t* r, const char* s, size_t nmatch,
                   regmatch_t* pm, int flags) {
    (void)flags;
    if (!r->pat || !s) return 1;
    size_t slen = strlen(s);
    size_t start = 0;
    for (;;) {
        int end = re_match(r->pat, 0, s, (int)start);
        if (end >= 0) {
            if (pm && nmatch > 0) { pm[0].rm_so = (int)start; pm[0].rm_eo = end; }
            return 0;
        }
        if (start >= slen) break;
        start++;
    }
    return 1;
}
#endif
