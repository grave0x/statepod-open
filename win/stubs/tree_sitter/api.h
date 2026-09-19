/* tree_sitter/api.h — compile-only stubs for the Windows kernel DLL.
 * Every AST op cleanly fails at ts_parser_new() == NULL; the traversal
 * stubs below exist only so kernel.c compiles.  Never linked on Linux
 * (the real libtree-sitter is used there). */
#ifndef SP_TS_API_H
#define SP_TS_API_H
#include <stdint.h>
#include <stddef.h>

typedef struct TSLanguage TSLanguage;
typedef struct TSParser TSParser;
typedef struct TSTree TSTree;
typedef struct TSQuery TSQuery;
typedef struct TSQueryCursor TSQueryCursor;

typedef struct TSPoint { uint32_t row, column; } TSPoint;
typedef struct TSNode {
    const void* tree;
    uint32_t    id;
    uint32_t    context[4];
} TSNode;
typedef struct TSTreeCursor {
    const void* tree;
    uint32_t    id;
    uint32_t    context[4];
} TSTreeCursor;
typedef struct TSQueryCapture { TSNode node; uint32_t index; } TSQueryCapture;
typedef struct TSQueryMatch {
    uint32_t id;
    uint16_t pattern_index;
    uint16_t capture_count;
    const TSQueryCapture* captures;
} TSQueryMatch;
typedef enum TSQueryError {
    TSQueryErrorNone = 0, TSQueryErrorSyntax, TSQueryErrorNodeType,
    TSQueryErrorField, TSQueryErrorCapture, TSQueryErrorStructure,
    TSQueryErrorLanguage
} TSQueryError;

#define ts_node_is_null(n) (1)
#define ts_node_child_count(n) (0u)
#define ts_node_start_byte(n) (0u)
#define ts_node_end_byte(n) (0u)

static inline TSParser* ts_parser_new(void) { return NULL; }
static inline void ts_parser_delete(TSParser* p) { (void)p; }
static inline void ts_parser_set_language(TSParser* p, const TSLanguage* l) { (void)p; (void)l; }
static inline TSTree* ts_parser_parse_string(TSParser* p, const TSTree* old,
                                             const char* src, uint32_t len) {
    (void)p; (void)old; (void)src; (void)len; return NULL;
}
static inline void ts_tree_delete(TSTree* t) { (void)t; }
static inline TSNode ts_tree_root_node(const TSTree* t) { (void)t; return (TSNode){0}; }
static inline const char* ts_node_type(TSNode n) { (void)n; return ""; }
static inline TSNode ts_node_child(TSNode n, uint32_t i) { (void)n; (void)i; return (TSNode){0}; }
static inline TSNode ts_node_child_by_field_name(TSNode n, const char* f, uint32_t l) {
    (void)n; (void)f; (void)l; return (TSNode){0};
}
static inline TSNode ts_node_parent(TSNode n) { (void)n; return (TSNode){0}; }
static inline char* ts_node_string(TSNode n) { (void)n; return NULL; }
static inline TSPoint ts_node_start_point(TSNode n) { (void)n; return (TSPoint){0,0}; }
static inline TSPoint ts_node_end_point(TSNode n) { (void)n; return (TSPoint){0,0}; }

static inline TSQuery* ts_query_new(const TSLanguage* l, const char* src,
                                    uint32_t len, uint32_t* off, TSQueryError* err) {
    (void)l; (void)src; (void)len; if (off) *off = 0; if (err) *err = TSQueryErrorNone;
    return NULL;
}
static inline void ts_query_delete(TSQuery* q) { (void)q; }
static inline const char* ts_query_capture_name_for_id(TSQuery* q, uint32_t id, uint32_t* len) {
    (void)q; (void)id; if (len) *len = 0; return "";
}
static inline TSQueryCursor* ts_query_cursor_new(void) { return NULL; }
static inline void ts_query_cursor_delete(TSQueryCursor* c) { (void)c; }
static inline void ts_query_cursor_exec(TSQueryCursor* c, TSQuery* q, TSNode n) {
    (void)c; (void)q; (void)n;
}
static inline int ts_query_cursor_next_match(TSQueryCursor* c, TSQueryMatch* m) {
    (void)c; (void)m; return 0;
}

static inline TSTreeCursor ts_tree_cursor_new(TSNode n) { (void)n; return (TSTreeCursor){0}; }
static inline void ts_tree_cursor_delete(TSTreeCursor* c) { (void)c; }
static inline TSNode ts_tree_cursor_current_node(const TSTreeCursor* c) { (void)c; return (TSNode){0}; }
static inline int ts_tree_cursor_goto_first_child(TSTreeCursor* c) { (void)c; return 0; }
static inline int ts_tree_cursor_goto_next_sibling(TSTreeCursor* c) { (void)c; return 0; }
static inline int ts_tree_cursor_goto_parent(TSTreeCursor* c) { (void)c; return 0; }

#endif
