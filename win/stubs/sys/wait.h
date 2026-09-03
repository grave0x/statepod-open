/* sys/wait.h — Windows shim: wait macros only (fork is stubbed out). */
#ifndef SS_WAIT_H
#define SS_WAIT_H
#include <sys/types.h>
#define WIFEXITED(s)   (((s) & 0xFF) == 0)
#define WEXITSTATUS(s) (((s) >> 8) & 0xFF)
#define WIFSIGNALED(s) (((s) & 0xFF) != 0 && ((s) & 0xFF) != 0x7F)
#define WTERMSIG(s)    ((s) & 0x7F)
#endif
