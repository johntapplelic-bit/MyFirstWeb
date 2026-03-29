param(
    [string]$Model = "llama3.2:3b",
    [switch]$SkipModelPull,
    [switch]$LocalOnly,
    [switch]$FreeFallbackOnly
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[Local AI] $Message" -ForegroundColor Cyan
}

function Import-EnvFile {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return
    }

    foreach ($rawLine in Get-Content -Path $Path) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            continue
        }

        $parts = $line.Split("=", 2)
        $key = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")

        if ($key) {
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
}

function Test-OllamaServer {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -Method Get -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Get-PrimaryIPv4 {
    try {
        $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.IPAddress -notlike "127.*" -and
                $_.IPAddress -notlike "169.254.*"
            } |
            Sort-Object InterfaceMetric |
            Select-Object -First 1 -ExpandProperty IPAddress

        if ($ip) {
            return $ip
        }
    } catch {
    }

    $match = ipconfig |
        Select-String -Pattern "IPv4 Address[^:]*:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)" |
        Select-Object -First 1

    if ($match -and $match.Matches.Count -gt 0) {
        return $match.Matches[0].Groups[1].Value
    }

    return "127.0.0.1"
}

function Ensure-FirewallRule {
    param([int]$Port)

    $ruleName = "MyFirstWeb LAN (port $Port)"

    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Step "Firewall rule '$ruleName' already exists."
        return
    }

    Write-Step "Firewall rule not found. Requesting elevated permission to add it..."

    $addRuleCmd = "New-NetFirewallRule -DisplayName '$ruleName' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Any | Out-Null; Write-Host 'Firewall rule added.'"

    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    if ($isAdmin) {
        Invoke-Expression $addRuleCmd
        Write-Step "Firewall rule '$ruleName' added."
    } else {
        try {
            Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command `"$addRuleCmd`"" -Verb RunAs -Wait -ErrorAction Stop
            Write-Step "Firewall rule '$ruleName' added via elevation."
        } catch {
            Write-Warning "Could not add firewall rule automatically. If LAN access fails, run this manually in an elevated PowerShell:"
            Write-Warning "  New-NetFirewallRule -DisplayName '$ruleName' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow"
        }
    }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$envPath = Join-Path $scriptDir ".env"
$templatePath = Join-Path $scriptDir "proxy.env.example"

if (-not (Test-Path $envPath)) {
    if (-not (Test-Path $templatePath)) {
        throw "Missing proxy.env.example. Cannot create .env."
    }

    Copy-Item -Path $templatePath -Destination $envPath -Force
    Write-Step "Created .env from proxy.env.example."
}

Import-EnvFile -Path $envPath

if ($LocalOnly) {
    $env:PROXY_HOST = "127.0.0.1"
} else {
    $env:PROXY_HOST = "0.0.0.0"
}

if (-not $env:PROXY_PORT) {
    $env:PROXY_PORT = "5051"
}

if (-not $env:OLLAMA_API_ENDPOINT) {
    $env:OLLAMA_API_ENDPOINT = "http://127.0.0.1:11434/api/chat"
}

$pythonExe = Join-Path $scriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Missing .venv Python at .venv\\Scripts\\python.exe. Create your venv first."
}

# Ensure the Windows Firewall allows LAN traffic on the proxy port (skipped in LocalOnly mode)
if (-not $LocalOnly) {
    Ensure-FirewallRule -Port ([int]$env:PROXY_PORT)
}

$ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCommand) {
    $defaultOllamaPath = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $defaultOllamaPath) {
        $ollamaCommand = [PSCustomObject]@{ Source = $defaultOllamaPath }
        Write-Step "Detected Ollama at $defaultOllamaPath"
    }
}
$ollamaAvailable = $null -ne $ollamaCommand

if ($FreeFallbackOnly) {
    $ollamaAvailable = $false
    Write-Step "Free fallback mode enabled: static pages and built-in browser AI replies only."
}

if ($ollamaAvailable) {
    if (-not (Test-OllamaServer)) {
        Write-Step "Starting Ollama background service..."
        Start-Process -FilePath $ollamaCommand.Source -ArgumentList "serve" -WindowStyle Hidden | Out-Null
        Write-Step "Waiting for Ollama to respond (up to 15 seconds)..."
        $ollamaReady = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Milliseconds 500
            if (Test-OllamaServer) { $ollamaReady = $true; break }
        }
        if (-not $ollamaReady) {
            throw "Ollama server did not respond after 15 seconds. Make sure Ollama is installed correctly."
        }
    } else {
        Write-Step "Ollama server already running."
    }

    if (-not $SkipModelPull) {
        Write-Step "Ensuring model $Model is available..."
        $modelExists = $false
        try {
            $null = & $ollamaCommand.Source show $Model 2>&1
            $modelExists = ($LASTEXITCODE -eq 0)
        } catch {
            $modelExists = $false
        }
        if (-not $modelExists) {
            Write-Step "Pulling model $Model (first run may take several minutes)..."
            $ErrorActionPreference = "Continue"
            & $ollamaCommand.Source pull $Model
            $pullExitCode = $LASTEXITCODE
            $ErrorActionPreference = "Stop"
            if ($pullExitCode -ne 0) {
                throw "Failed to pull model '$Model'. Check your internet connection and try again."
            }
        }
    }
} else {
    Write-Warning "Ollama is not available. Running in free fallback mode (no paid APIs required)."
    Write-Warning "Chemistry live provider calls will not work unless Ollama or another provider is configured."
}

$port = [int]$env:PROXY_PORT
$proxyAlreadyRunning = $false

try {
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listeners) {
        $proxyAlreadyRunning = $true
    }
} catch {
}

if (-not $proxyAlreadyRunning) {
    Write-Step "Starting Flask proxy server..."
    $proxyScript = Join-Path $scriptDir "proxy_server.py"
    $commandText = "Set-Location -Path '$scriptDir'; " +
        '$env:PROXY_HOST="' + $env:PROXY_HOST + '"; ' +
        '$env:PROXY_PORT="' + $env:PROXY_PORT + '"; ' +
        '$env:OLLAMA_API_ENDPOINT="' + $env:OLLAMA_API_ENDPOINT + '"; ' +
        "& '$pythonExe' '$proxyScript'"

    Start-Process -FilePath "powershell.exe" -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $commandText -WorkingDirectory $scriptDir | Out-Null
} else {
    Write-Step "Proxy already running on port $port."
}

$healthUrl = "http://127.0.0.1:$port/api/health"
$healthy = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2 | Out-Null
        $healthy = $true
        break
    } catch {
        if ($attempt -eq 20) { Write-Step "Still waiting for proxy to start..." }
        Start-Sleep -Milliseconds 500
    }
}

if (-not $healthy) {
    Write-Warning "Proxy health endpoint did not respond yet. Wait a few seconds and retry if needed."
}

$ipAddress = if ($env:PROXY_HOST -eq "127.0.0.1") { "127.0.0.1" } else { Get-PrimaryIPv4 }
$appUrl = "http://{0}:{1}/" -f $ipAddress, $port

Write-Host ""
Write-Host "Local AI is ready." -ForegroundColor Green
Write-Host "Open on this PC: http://127.0.0.1:$port/"
if ($env:PROXY_HOST -ne "127.0.0.1") {
    Write-Host "Open on iPhone/iPad (same Wi-Fi): $appUrl"
}
if (-not $ollamaAvailable) {
    Write-Host "Mode: Free fallback only (built-in page responses, no paid APIs)." -ForegroundColor Yellow
}

Start-Process $appUrl | Out-Null
