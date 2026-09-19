/* exec_win_test.c — wine acceptance test for the Windows EXECUTE path.
 *
 * Usage: exec_win_test.exe <absolute-root-dir>
 * PATH must point at a directory containing powershell.exe (the wine test
 * shim).  Verifies:
 *   1) allowed cmdlet runs (command line built correctly)
 *   2) allowed cmdlet with args runs
 *   3) non-allowlisted command is denied before spawn
 *   4) write-capable parameter is denied before spawn
 */
#include "kernel.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures = 0;

static void check(int ok, const char* name, const char* detail)
{
    printf("%s %-48s %s\n", ok ? "PASS" : "FAIL", name, detail);
    if (!ok) failures++;
}

static SP_Result* run_exec(RepoState* st, const char* cmd)
{
    SP_Operation op;
    SP_Plan plan;
    memset(&op, 0, sizeof(op));
    op.type = SP_OP_EXECUTE;
    op.command = cmd;
    memset(&plan, 0, sizeof(plan));
    plan.ops = &op;
    plan.op_count = 1;
    return sp_execute(st, &plan);
}

int main(int argc, char** argv)
{
    const char* root = (argc > 1) ? argv[1] : ".";
    char detail[512];
    SP_Result* r;
    RepoState* st = sp_state_new(root);
    if (!st) { printf("FAIL state_new (%s)\n", root); return 1; }

    /* 1: allowed cmdlet, no args */
    r = run_exec(st, "Get-Process");
    check(r->exit_code == 0, "Get-Process allowed",
          r->log_count > 0 && r->logs[0] ? r->logs[0] : "(no log)");
    if (r->log_count > 0 && r->logs[0])
        check(strstr(r->logs[0], "-Command") != NULL, "Get-Process via -Command",
              r->logs[0]);
    check(strstr(r->logs[0], "Get-Process") != NULL, "Get-Process in cmdline",
          r->logs[0]);
    sp_result_free(r);

    /* 2: allowed cmdlet with validated args */
    r = run_exec(st, "Get-ChildItem -Path README.md");
    check(r->exit_code == 0, "Get-ChildItem -Path README.md allowed",
          r->log_count > 0 && r->logs[0] ? r->logs[0] : "(no log)");
    if (r->log_count > 0 && r->logs[0])
        check(strstr(r->logs[0], "Get-ChildItem -Path README.md") != NULL,
              "args joined in cmdline", r->logs[0]);
    sp_result_free(r);

    /* 3: command NOT on the allowlist -> denied pre-spawn */
    r = run_exec(st, "Remove-Item README.md");
    check(r->exit_code != 0, "Remove-Item denied (not whitelisted)",
          r->error_message ? r->error_message : "(no msg)");
    check(r->error_message && strstr(r->error_message, "not whitelisted") != NULL,
          "denial reason mentions whitelist", r->error_message ? r->error_message : "");
    sp_result_free(r);

    /* 4: write-capable parameter -> denied pre-spawn */
    r = run_exec(st, "Get-ChildItem -Path . -OutFile pwn.txt");
    check(r->exit_code != 0, "-OutFile denied (write param)",
          r->error_message ? r->error_message : "(no msg)");
    sp_result_free(r);

    /* 5: safe cmdlet with a dash-arg the deny list allows */
    r = run_exec(st, "Get-Content -Path README.md -Tail 2");
    check(r->exit_code == 0, "Get-Content -Tail allowed",
          r->log_count > 0 && r->logs[0] ? r->logs[0] : "(no log)");
    sp_result_free(r);

    /* 6: shell metacharacter in arg -> denied by token_is_safe */
    r = run_exec(st, "Get-Process | Stop-Process");
    check(r->exit_code != 0, "pipe metachar denied",
          r->error_message ? r->error_message : "(no msg)");
    sp_result_free(r);

    /* 7: service management is a WRITE op -> denied by default + escalation */
    r = run_exec(st, "Restart-Service Spooler");
    check(r->exit_code != 0, "Restart-Service denied (mgmt disabled)",
          r->error_message ? r->error_message : "(no msg)");
    check(r->needs_escalation, "Restart-Service flags escalation",
          r->escalation_reason ? r->escalation_reason : "(no reason)");
    sp_result_free(r);

    /* 8: opt-in at the embedding layer -> allowed */
    sp_state_set_allow_service_mgmt(st, 1);
    r = run_exec(st, "Restart-Service Spooler");
    check(r->exit_code == 0, "Restart-Service allowed after opt-in",
          r->log_count > 0 && r->logs[0] ? r->logs[0] : "(no log)");
    if (r->log_count > 0 && r->logs[0])
        check(strstr(r->logs[0], "Restart-Service Spooler") != NULL,
              "service cmd in cmdline", r->logs[0]);
    sp_result_free(r);

    /* 9: write param still denied even when mgmt is enabled */
    r = run_exec(st, "Stop-Service Spooler -OutFile pwn.txt");
    check(r->exit_code != 0, "-OutFile denied with mgmt enabled",
          r->error_message ? r->error_message : "(no msg)");
    sp_result_free(r);

    /* 10: read-only cmdlet unaffected by the gate */
    r = run_exec(st, "Get-Service Spooler");
    check(r->exit_code == 0, "Get-Service still allowed",
          r->log_count > 0 && r->logs[0] ? r->logs[0] : "(no log)");
    sp_result_free(r);

    sp_state_set_allow_service_mgmt(st, 0);   /* back to default */
    sp_state_free(st);
    printf(failures ? "\n%d FAILURES\n" : "\nALL PASS\n", failures);
    return failures ? 1 : 0;
}
