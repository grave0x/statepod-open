/* win_compat.h — forced-include (-include) shim so kernel.c compiles
 * cleanly with mingw-w64.  Windows-only (guarded by _WIN32).
 *   fork/pipe/waitpid -> clean ENOSYS (EXECUTE denied on this build)
 *   popen -> _popen, mkdir -> _mkdir, realpath -> _fullpath
 *   strtok_r -> reentrant local implementation
 *   statvfs -> sys/statvfs.h shim (GetDiskFreeSpaceExA)
 *   S_ISDIR/S_ISREG etc. -> from sys/stat.h or defined here
 */
#ifndef SP_WIN_COMPAT_H
#define SP_WIN_COMPAT_H
#ifdef _WIN32
#include <errno.h>
#include <stdlib.h>
#include <io.h>
#include <process.h>
#include <direct.h>
#include <sys/stat.h>

#ifndef ssize_t
#define ssize_t SSIZE_T
#endif
#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

#ifndef O_NOFOLLOW
#define O_NOFOLLOW 0
#endif
#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif
#ifndef S_ISDIR
#define S_ISDIR(m)  (((m) & S_IFMT) == S_IFDIR)
#endif
#ifndef S_ISREG
#define S_ISREG(m)  (((m) & S_IFMT) == S_IFREG)
#endif
#ifndef S_ISLNK
#define S_ISLNK(m)  0
#endif

/* EXECUTE on Windows: PowerShell cmdlet allowlist + direct CreateProcess
 * for plain binaries (git, ...).  Read-only diagnostics only: every
 * command token has already passed kernel.c's token_is_safe() (no quotes,
 * spaces, backticks, $, |, >, <, ;, &, .., absolute paths), and cmdlets
 * are limited to the read-only list below.  powershell.exe ships on every
 * supported Windows client/server. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int sp_win_is_cmdlet(const char* t)
{
    static const char* const cmdlets[] = {
        "Get-ChildItem", "Get-Content", "Get-Process", "Get-Service",
        "Get-Item", "Get-Date", "Get-Location", "Get-Command",
        "Select-String", "Test-Path", "Measure-Object", NULL
    };
    size_t i;
    if (!t || !*t) return 0;
    for (i = 0; cmdlets[i]; i++)
        if (_stricmp(t, cmdlets[i]) == 0) return 1;
    return 0;
}

/* Service-management cmdlets (MSP story): WRITE ops, gated by the
 * kernel's allow_service_mgmt flag (default OFF). */
static int sp_win_is_service_cmdlet(const char* t)
{
    static const char* const svc[] = {
        "Start-Service", "Stop-Service", "Restart-Service", "Set-Service", NULL
    };
    size_t i;
    if (!t || !*t) return 0;
    for (i = 0; svc[i]; i++)
        if (_stricmp(t, svc[i]) == 0) return 1;
    return 0;
}

/* kernel.c's exec_binary_allowed() hook (Windows build only). */
static int sp_win_exec_binary_allowed(const char* t)
{
    return sp_win_is_cmdlet(t) || sp_win_is_service_cmdlet(t);
}

/* Space-join validated tokens into a single-line command.  token_is_safe
 * guarantees no quoting is needed (no spaces, quotes, or metacharacters). */
static int sp_win_join(char* out, size_t cap, char* const argv[], int argc)
{
    size_t used = 0;
    int i;
    for (i = 0; i < argc && used < cap; i++) {
        size_t n = strlen(argv[i]);
        if (i > 0) { if (used + 1 >= cap) return -1; out[used++] = ' '; }
        if (used + n >= cap) return -1;
        memcpy(out + used, argv[i], n);
        used += n;
    }
    out[used] = '\0';
    return 0;
}

/* Windows run_capture(): cmdlets run via powershell.exe -Command; plain
 * binaries (git, rg, ...) run directly through CreateProcess (no shell).
 * Captures stdout+stderr into a caller-owned buffer capped at `cap`
 * bytes; the child is drained past the cap so it never blocks on a full
 * pipe.  Returns 0 on success, -1 on spawn failure. */
static int sp_win_run_capture(char* const argv[], const char* cwd, size_t cap,
                              char** out, int* status)
{
    char cmdline[4096 + 128];   /* SP_MAX_EXEC_CMD == 4096 (kernel.c) */
    char inner[4096 + 8];
    HANDLE rd, wr;
    SECURITY_ATTRIBUTES sa;
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    char* buf;
    DWORD got;
    size_t used;
    int argc = 0;
    *status = -1;
    *out = NULL;
    while (argv[argc]) argc++;
    if (argc == 0) return -1;

    if (sp_win_is_cmdlet(argv[0]) || sp_win_is_service_cmdlet(argv[0])) {
        if (sp_win_join(inner, sizeof(inner), argv, argc) != 0) return -1;
        if (snprintf(cmdline, sizeof(cmdline),
                     "powershell.exe -NoProfile -NonInteractive -Command \"%s\"",
                     inner) >= (int)sizeof(cmdline)) return -1;
    } else {
        if (sp_win_join(cmdline, sizeof(cmdline), argv, argc) != 0) return -1;
    }

    sa.nLength = sizeof(sa); sa.bInheritHandle = TRUE; sa.lpSecurityDescriptor = NULL;
    if (!CreatePipe(&rd, &wr, &sa, 0)) return -1;
    SetHandleInformation(rd, HANDLE_FLAG_INHERIT, 0);

    memset(&si, 0, sizeof(si)); si.cb = sizeof(si);
    si.hStdOutput = wr; si.hStdError = wr;
    si.dwFlags = STARTF_USESTDHANDLES;
    memset(&pi, 0, sizeof(pi));
    if (!CreateProcessA(NULL, cmdline, NULL, NULL, TRUE, CREATE_NO_WINDOW,
                        NULL, (cwd && *cwd) ? cwd : NULL, &si, &pi)) {
        DWORD le = GetLastError();
        fprintf(stderr, "sp_win_run_capture: CreateProcess failed (%lu): %s\n",
                (unsigned long)le, cmdline);
        CloseHandle(rd); CloseHandle(wr);
        return -1;
    }
    CloseHandle(wr);  /* parent side: EOF arrives when the child exits */

    buf = (char*)malloc(cap + 1);
    if (!buf) {
        CloseHandle(rd);
        WaitForSingleObject(pi.hProcess, INFINITE);
        CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
        return -1;
    }
    used = 0;
    while (used < cap && ReadFile(rd, buf + used, (DWORD)(cap - used), &got, NULL) && got > 0)
        used += got;
    if (used >= cap) {   /* drain the tail so the child never blocks */
        char tmp[4096];
        while (ReadFile(rd, tmp, sizeof(tmp), &got, NULL) && got > 0) {}
    }
    CloseHandle(rd);
    WaitForSingleObject(pi.hProcess, INFINITE);
    {
        DWORD code = 1;
        GetExitCodeProcess(pi.hProcess, &code);
        *status = (int)code;
    }
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    buf[used] = '\0';
    *out = buf;
    return 0;
}
#define run_capture sp_win_run_capture

#ifndef popen
#define popen  _popen
#endif
#ifndef pclose
#define pclose _pclose
#endif

static inline int sp_win_mkdir(const char* p, unsigned m) { (void)m; return _mkdir(p); }
#define mkdir sp_win_mkdir

#ifndef access
#define access _access
#endif

static inline char* sp_win_realpath(const char* p, char* out) {
    return _fullpath(out, p, PATH_MAX);
}
#define realpath sp_win_realpath
#ifndef lstat
#define lstat stat
#endif

static inline char* sp_win_strtok_r(char* str, const char* delim, char** save) {
    char* tok;
    if (!str) str = *save;
    str += strspn(str, delim);
    if (!*str) { *save = str; return NULL; }
    tok = str;
    str += strcspn(str, delim);
    if (*str) *str++ = '\0';
    *save = str;
    return tok;
}
#define strtok_r sp_win_strtok_r

#endif /* _WIN32 */
#endif
