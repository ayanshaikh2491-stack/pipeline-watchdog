<#
.SYNOPSIS
  Deploy the Pipeline Watchdog service to Render.

.DESCRIPTION
  This script pushes the watchdog code to a GitHub repo and deploys it
  to Render via the Render CLI. Run it from the watchdog/ directory.

  Prerequisites:
    - Git installed and configured
    - GitHub CLI (gh) installed and authenticated, OR a GitHub repo already created
    - Render CLI installed and authenticated (run `render whoami` to verify)

  If you prefer manual deployment, see DEPLOY.md for step-by-step instructions.
#>

$ErrorActionPreference = "Stop"
$WatchdogDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoName = "pipeline-watchdog"

Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║         Pipeline Watchdog — Render Deploy Script        ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Step 0: Verify prerequisites ──
Write-Host "🔍 Checking prerequisites..." -ForegroundColor Yellow

$renderOk = $false
try {
    $renderVersion = render --version 2>$null
    $renderWhoami = render whoami 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✅ Render CLI: $renderVersion" -ForegroundColor Green
        Write-Host "  ✅ Authenticated as: $renderWhoami" -ForegroundColor Green
        $renderOk = $true
    }
} catch {
    Write-Host "  ❌ Render CLI not found or not authenticated." -ForegroundColor Red
}

$ghOk = $false
try {
    $ghStatus = gh auth status 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✅ GitHub CLI (gh) authenticated" -ForegroundColor Green
        $ghOk = $true
    }
} catch {
    Write-Host "  ❌ GitHub CLI not authenticated or not installed." -ForegroundColor Red
}

# Check if we're in a git repo
$inGitRepo = (git rev-parse --is-inside-work-tree 2>$null) -eq "true"
if (-not $inGitRepo) {
    Write-Host "  ℹ️  Not inside a Git repo — will create one." -ForegroundColor Yellow
}

Write-Host ""

# ── Step 1: Prepare the repo ──
Write-Host "📦 Step 1: Preparing Git repo..." -ForegroundColor Yellow

if (-not $inGitRepo) {
    git -C $WatchdogDir init
    git -C $WatchdogDir add -A
    git -C $WatchdogDir commit -m "Initial commit: Pipeline Watchdog v2"
    Write-Host "  ✅ Local Git repo initialized and committed." -ForegroundColor Green
}

# ── Step 2: Push to GitHub ──
Write-Host "🌐 Step 2: Pushing to GitHub..." -ForegroundColor Yellow

if ($ghOk) {
    # Check if remote already exists
    $remoteUrl = git -C $WatchdogDir remote get-url origin 2>$null
    if (-not $remoteUrl) {
        Write-Host "  Creating GitHub repo '$RepoName'..." -ForegroundColor Cyan
        gh repo create "$RepoName" --public --source=$WatchdogDir --push --remote=origin
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  ✅ Repo created and code pushed!" -ForegroundColor Green
        } else {
            Write-Host "  ⚠️  Could not create repo. Create it manually at:" -ForegroundColor Yellow
            Write-Host "     https://github.com/new"
            Write-Host "  Then run:" -ForegroundColor Yellow
            Write-Host "     git -C $WatchdogDir remote add origin https://github.com/YOUR_USER/$RepoName.git"
            Write-Host "     git -C $WatchdogDir push -u origin main"
        }
    } else {
        Write-Host "  Remote already exists: $remoteUrl" -ForegroundColor Cyan
        git -C $WatchdogDir push -u origin HEAD
        Write-Host "  ✅ Code pushed to existing remote!" -ForegroundColor Green
    }
} else {
    Write-Host "  ⚠️  Skipping GitHub push (gh CLI not available)." -ForegroundColor Yellow
    Write-Host "  Create a repo at https://github.com/new and run:" -ForegroundColor Yellow
    Write-Host "     git -C $WatchdogDir remote add origin https://github.com/YOUR_USER/$RepoName.git"
    Write-Host "     git -C $WatchdogDir push -u origin main"
}

Write-Host ""

# ── Step 3: Deploy to Render ──
Write-Host "🚀 Step 3: Deploying to Render..." -ForegroundColor Yellow

if ($renderOk -and $ghOk) {
    $remoteUrl = git -C $WatchdogDir remote get-url origin 2>$null
    if ($remoteUrl) {
        Write-Host "  Creating service on Render..." -ForegroundColor Cyan

        # Read render.yaml to parse configuration
        $config = Get-Content "$WatchdogDir\render.yaml"

        render services create `
            --name "pipeline-watchdog" `
            --type web_service `
            --repo "$remoteUrl" `
            --runtime python `
            --region singapore `
            --plan free `
            --build-command "pip install -r requirements.txt" `
            --start-command "uvicorn main:app --host 0.0.0.0 --port 10000" `
            --health-check-path "/health" `
            --env-var "EC2_URL=http://18.213.66.136:8000" `
            --env-var "N8N_URL=https://nexus-n8n-x17d.onrender.com" `
            --env-var "N8N_WEBHOOK_PATHS=/webhook/test,/webhook/prod" `
            --env-var "CHECK_INTERVAL=90" `
            --auto-deploy `
            --output json

        if ($LASTEXITCODE -eq 0) {
            Write-Host "  ✅ Service created! Now set secrets in the Render dashboard:" -ForegroundColor Green
            Write-Host "     GMAIL_ADDRESS" -ForegroundColor Yellow
            Write-Host "     GMAIL_APP_PASSWORD" -ForegroundColor Yellow
            Write-Host "  Go to https://dashboard.render.com -> select your service -> Environment" -ForegroundColor Yellow
        } else {
            Write-Host "  ⚠️  Service creation failed. Try the manual steps in DEPLOY.md" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  ⚠️  No Git remote found. Deploy manually via DEPLOY.md" -ForegroundColor Yellow
    }
} else {
    Write-Host "  ⚠️  Cannot deploy automatically." -ForegroundColor Yellow
    Write-Host "  See DEPLOY.md for manual deployment steps." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Done! After deploying, set GMAIL_ADDRESS and            ║" -ForegroundColor Cyan
Write-Host "║  GMAIL_APP_PASSWORD as secret env vars in the Render    ║" -ForegroundColor Cyan
Write-Host "║  dashboard. The service will start at:                  ║" -ForegroundColor Cyan
Write-Host "║  https://pipeline-watchdog.onrender.com                 ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
