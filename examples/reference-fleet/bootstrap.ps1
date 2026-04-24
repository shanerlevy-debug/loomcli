# bootstrap.ps1 — PowerShell equivalent of bootstrap.sh.
# Deploys the Powerloom reference fleet end-to-end on Windows.
#
# Prerequisites:
#   1. `pip install loomcli>=0.5.2` (requires weave skill upload commands)
#   2. `weave login` — signed into your target control plane
#   3. An existing root OU on your account (default /bespoke-technology;
#      override with $env:OU_ROOT)
#   4. PowerShell execution policy must allow local scripts. If it doesn't:
#        Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
#      OR invoke like this each time:
#        powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
#
# Usage:
#   .\bootstrap.ps1                            # default root /bespoke-technology
#   $env:OU_ROOT = "/my-org"; .\bootstrap.ps1  # different root
#   .\bootstrap.ps1 -SchemaVersion v1.2.0      # force older schema
#   .\bootstrap.ps1 -DryRun                    # preview without applying

[CmdletBinding()]
param(
    [string]$SchemaVersion = "v2.0.0",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

$ouRoot = if ($env:OU_ROOT) { $env:OU_ROOT } else { "/bespoke-technology" }
$scriptDir = $PSScriptRoot
$fleetDir = Join-Path $scriptDir $SchemaVersion
$archivesDir = Join-Path $scriptDir "skill-archives"

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Host "error: required command '$Name' not found in PATH" -ForegroundColor Red
        exit 1
    }
}

Require-Command "weave"

if (-not (Test-Path $fleetDir)) {
    Write-Host "error: fleet manifests not found at $fleetDir" -ForegroundColor Red
    Write-Host "       (did you pass a valid -SchemaVersion? Tried: $SchemaVersion)" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $archivesDir)) {
    Write-Host "error: skill archives not found at $archivesDir" -ForegroundColor Red
    exit 1
}

Write-Host "==> Reference-fleet bootstrap"
Write-Host "    OU root:          $ouRoot"
Write-Host "    Schema version:   $SchemaVersion"
Write-Host "    Fleet manifests:  $fleetDir"
Write-Host "    Skill archives:   $archivesDir"
Write-Host "    Dry run:          $DryRun"
Write-Host ""

# Verify sign-in. `weave auth whoami` exits non-zero when not signed in.
$whoamiOutput = & weave auth whoami 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "error: not signed in. Run 'weave login' first." -ForegroundColor Red
    exit 1
}
Write-Host "==> Signed in as: $whoamiOutput"
Write-Host ""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Invoke-Weave {
    <#
    .SYNOPSIS
      Wrap weave invocation with dry-run support + error propagation.
      Emits output indented 4 spaces for readability.
    #>
    param(
        [Parameter(Mandatory, Position = 0)][string[]]$WeaveArgs,
        [string]$DryRunPreview
    )
    if ($DryRun) {
        if ($DryRunPreview) {
            Write-Host "    [dry-run] $DryRunPreview"
        } else {
            Write-Host "    [dry-run] weave $($WeaveArgs -join ' ')"
        }
        return
    }
    $output = & weave @WeaveArgs 2>&1
    $exitCode = $LASTEXITCODE
    $output | ForEach-Object { Write-Host "    $_" }
    if ($exitCode -ne 0) {
        Write-Host "    (weave exited with code $exitCode)" -ForegroundColor Red
        exit $exitCode
    }
}

function Apply-Manifest {
    param([string]$Path, [string]$Label)
    Write-Host "  $Label"
    if ($DryRun) {
        Invoke-Weave -WeaveArgs @("plan", "-f", $Path)
    } else {
        Invoke-Weave -WeaveArgs @("apply", "-f", $Path)
    }
}

function Build-Archive {
    <#
    .SYNOPSIS
      Zip the skill-archives/<name>/ directory contents into a temp .zip
      with SKILL.md at the root (required by the API's archive format).
    #>
    param([string]$SkillName)
    $src = Join-Path $archivesDir $SkillName
    if (-not (Test-Path $src)) {
        throw "archive source $src not found"
    }
    $skillMd = Join-Path $src "SKILL.md"
    if (-not (Test-Path $skillMd)) {
        throw "$skillMd missing (required by archive format)"
    }
    $timestamp = [int][double]::Parse((Get-Date -UFormat %s))
    $out = Join-Path $env:TEMP "$SkillName-$timestamp.zip"
    if (Test-Path $out) { Remove-Item -Force $out }
    # Compress-Archive with "<src>\*" packs contents at the zip root
    # (not the directory itself), which is what skill_storage expects.
    Compress-Archive -Path (Join-Path $src "*") -DestinationPath $out -Force
    return $out
}

function Get-OuPathFromManifest {
    <#
    .SYNOPSIS
      Pull metadata.ou_path out of a YAML manifest without a YAML parser.
      All fleet manifests follow the same shape, so a regex match is
      reliable here.
    #>
    param([string]$Path)
    $content = Get-Content -Path $Path -Raw
    if ($content -match '(?m)^\s+ou_path:\s+(\S+)') {
        return $Matches[1]
    }
    throw "could not locate ou_path in $Path"
}

# ---------------------------------------------------------------------------
# Step 1: Apply OU manifests
# ---------------------------------------------------------------------------

Write-Host "==> Step 1/4: Apply OU manifests"
Get-ChildItem -Path (Join-Path $fleetDir "ous") -Filter "*.yaml" | Sort-Object Name | ForEach-Object {
    Apply-Manifest -Path $_.FullName -Label ([IO.Path]::GetFileNameWithoutExtension($_.Name))
}

# ---------------------------------------------------------------------------
# Step 2: Apply skill shells
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "==> Step 2/4: Apply Skill manifests (shells with current_version_id: null)"
Get-ChildItem -Path (Join-Path $fleetDir "skills") -Filter "*.yaml" | Sort-Object Name | ForEach-Object {
    Apply-Manifest -Path $_.FullName -Label ([IO.Path]::GetFileNameWithoutExtension($_.Name))
}

# ---------------------------------------------------------------------------
# Step 3: Upload-and-activate archives
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "==> Step 3/4: Upload + activate skill archives"
Get-ChildItem -Path (Join-Path $fleetDir "skills") -Filter "*.yaml" | Sort-Object Name | ForEach-Object {
    $skillName = [IO.Path]::GetFileNameWithoutExtension($_.Name)
    $ouPath = Get-OuPathFromManifest -Path $_.FullName
    $address = "$ouPath/$skillName"

    $archive = $null
    try {
        $archive = Build-Archive -SkillName $skillName
        Write-Host "  $address <- $archive"
        Invoke-Weave -WeaveArgs @("skill", "upload-and-activate", $address, $archive)
    } finally {
        if ($archive -and (Test-Path $archive)) {
            Remove-Item -Force $archive -ErrorAction SilentlyContinue
        }
    }
}

# ---------------------------------------------------------------------------
# Step 4: Apply agent manifests
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "==> Step 4/4: Apply Agent manifests (reference skills by name)"
Get-ChildItem -Path (Join-Path $fleetDir "agents") -Filter "*.yaml" | Sort-Object Name | ForEach-Object {
    Apply-Manifest -Path $_.FullName -Label ([IO.Path]::GetFileNameWithoutExtension($_.Name))
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

$ouCount = (Get-ChildItem -Path (Join-Path $fleetDir "ous") -Filter "*.yaml" -ErrorAction SilentlyContinue).Count
$skillCount = (Get-ChildItem -Path (Join-Path $fleetDir "skills") -Filter "*.yaml" -ErrorAction SilentlyContinue).Count
$agentCount = (Get-ChildItem -Path (Join-Path $fleetDir "agents") -Filter "*.yaml" -ErrorAction SilentlyContinue).Count

Write-Host ""
Write-Host "==> Done. Fleet summary:"
Write-Host "    OUs:    $ouCount"
Write-Host "    Skills: $skillCount"
Write-Host "    Agents: $agentCount"
Write-Host ""
Write-Host "Verify:"
Write-Host "    weave get ou"
Write-Host "    weave get skill"
Write-Host "    weave get agent"
