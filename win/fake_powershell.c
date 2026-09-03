/* fake_powershell.c — wine test shim: stands in for powershell.exe and
 * prints its full command line, so the runner's command construction can
 * be verified end-to-end under wine (which has no real PowerShell). */
#include <stdio.h>

int main(int argc, char** argv)
{
    for (int i = 1; i < argc; i++)
        printf("%s%s", i > 1 ? " " : "", argv[i]);
    printf("\n");
    return 0;
}
