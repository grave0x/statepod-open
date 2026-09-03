/* infer/plan_gbnf.h — generated from infer/plan.gbnf (do not edit by hand).
 * The plan schema grammar, llama.cpp-safe (single-line rules, no underscores).
 */
static const char SS_PLAN_GBNF[] =
    "# SwarmState plan grammar — flattened for llama.cpp's GBNF parser.\n"
    "# Same schema as harness/plan_grammar.py PLAN_GBNF.  llama.cpp GBNF:\n"
    "# single-line rules and NO underscores in rule names.\n"
    "root      ::= ws \"{\" ws ops \",\" ws strategy ws \"}\"\n"
    "ops       ::= \"\\\"ops\\\"\" ws \":\" ws \"[\" ws op (ws \",\" ws op)* ws \"]\"\n"
    "op        ::= \"{\" ws \"\\\"type\\\"\" ws \":\" ws optype (ws \",\" ws opfield)* ws \"}\"\n"
    "optype    ::= \"\\\"READ\\\"\" | \"\\\"WRITE\\\"\" | \"\\\"GREP\\\"\" | \"\\\"DIFF\\\"\" | \"\\\"STATUS\\\"\" | \"\\\"EXECUTE\\\"\" | \"\\\"AST_PARSE\\\"\" | \"\\\"AST_QUERY\\\"\" | \"\\\"SYMBOL_SUMMARY\\\"\"\n"
    "opfield   ::= \"\\\"path\\\"\" ws \":\" ws string | \"\\\"content\\\"\" ws \":\" ws string | \"\\\"pattern\\\"\" ws \":\" ws string | \"\\\"target\\\"\" ws \":\" ws string | \"\\\"command\\\"\" ws \":\" ws string | \"\\\"linestart\\\"\" ws \":\" ws number | \"\\\"lineend\\\"\" ws \":\" ws number | \"\\\"maxresults\\\"\" ws \":\" ws number\n"
    "strategy  ::= \"\\\"strategy\\\"\" ws \":\" ws (\"\\\"DELTA\\\"\" | \"\\\"TARGETED\\\"\" | \"\\\"FULL\\\"\" | \"\\\"SYMBOLIC\\\"\")\n"
    "string    ::= \"\\\"\" char* \"\\\"\"\n"
    "char      ::= [^\"\\\\] | \"\\\\\" escape\n"
    "escape    ::= [\"\\\\/bfnrt]\n"
    "number    ::= \"-\"? [0-9]+\n"
    "ws        ::= [ \\t\\n]*\n";
