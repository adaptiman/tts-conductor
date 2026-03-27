#requires -Version 7.0

[CmdletBinding()]
param(
    [string]$Remote = "ssh-remote+cookbook-vm",
    [string]$RemotePath = "/home/adaptiman/tts-conductor",
    [switch]$ReuseWindow,
    [switch]$NewWindow,
    [int]$ReadyTimeoutSeconds = 15,
    [switch]$SkipReadyWait
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-CodeCommand {
    $code = Get-Command code -ErrorAction SilentlyContinue
    if ($code) {
        return $code.Source
    }

    $candidates = @(
        "$env:LOCALAPPDATA\\Programs\\Microsoft VS Code\\bin\\code.cmd",
        "$env:ProgramFiles\\Microsoft VS Code\\bin\\code.cmd",
        "$env:ProgramFiles(x86)\\Microsoft VS Code\\bin\\code.cmd"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    throw "Could not find 'code' on PATH or in default VS Code install locations."
}

function Get-RemoteHostAlias {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Remote
    )

    if ($Remote -match "^[^+]+\+(.+)$") {
        return $Matches[1]
    }

    return $Remote
}

function Wait-ForRemoteServerReady {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HostName,
        [Parameter(Mandatory = $true)]
        [int]$TimeoutSeconds
    )

    $ssh = Get-Command ssh -ErrorAction SilentlyContinue
    if (-not $ssh) {
        Write-Warning "Skipping readiness wait because 'ssh' is not available on PATH."
        return
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    Write-Host "Waiting up to $TimeoutSeconds seconds for remote VS Code server to report ready..."

    while ((Get-Date) -lt $deadline) {
        $probeOutput = & $ssh.Source -o BatchMode=yes -o ConnectTimeout=3 $HostName "sh -lc 'log=`$(ls -1t ~/.vscode-server/.cli.*.log 2>/dev/null | head -n1); if [ -n \"`$log\" ] && grep -q \"listeningOn==\" \"`$log\"; then echo READY; else echo WAIT; fi'" 2>$null
        if ($LASTEXITCODE -eq 0 -and $probeOutput -match "READY") {
            Write-Host "Remote VS Code server is ready."
            return
        }

        Start-Sleep -Milliseconds 700
    }

    Write-Warning "Timed out waiting for remote server readiness. VS Code may still be connecting."
}

if ($ReuseWindow -and $NewWindow) {
    throw "Use only one of -ReuseWindow or -NewWindow."
}

if ($ReadyTimeoutSeconds -lt 1) {
    throw "ReadyTimeoutSeconds must be >= 1."
}

$codeCmd = Get-CodeCommand
$windowArg = if ($NewWindow) { "--new-window" } else { "--reuse-window" }
$folderUri = "vscode-remote://$Remote$RemotePath"
$remoteHost = Get-RemoteHostAlias -Remote $Remote

Write-Host "Launching VS Code: $Remote -> $RemotePath"
& $codeCmd $windowArg --folder-uri $folderUri

if (-not $SkipReadyWait) {
    Wait-ForRemoteServerReady -HostName $remoteHost -TimeoutSeconds $ReadyTimeoutSeconds
}