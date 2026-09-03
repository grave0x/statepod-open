/* sys/statvfs.h — Windows shim: disk info via GetDiskFreeSpaceExA. */
#ifndef SS_STATVFS_H
#define SS_STATVFS_H
#include <sys/types.h>
#include <windows.h>
struct statvfs {
    unsigned long f_bsize;
    unsigned long f_frsize;
    unsigned long long f_blocks;
    unsigned long long f_bfree;
    unsigned long long f_bavail;
};
static inline int statvfs(const char* path, struct statvfs* out) {
    ULARGE_INTEGER freeA, total, freeB;
    if (!GetDiskFreeSpaceExA(path && *path ? path : ".", &freeA, &total, &freeB)) return -1;
    out->f_bsize = 4096; out->f_frsize = 4096;
    out->f_blocks = (unsigned long long)(total.QuadPart / 4096);
    out->f_bfree  = (unsigned long long)(freeA.QuadPart / 4096);
    out->f_bavail = (unsigned long long)(freeA.QuadPart / 4096);
    return 0;
}
#endif
