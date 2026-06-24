# run.ps1 — Universal automation runner for RMEDA Service

# 1. Verify python installation
$pythonCheck = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $pythonCheck) {
    Write-Host "ERROR: Python is not installed or not in your PATH. Please install Python and try again." -ForegroundColor Red
    Exit 1
}

Write-Host "Python detected: $(python --version)" -ForegroundColor Green

# 2. Check for configuration file
if (-not (Test-Path ".env")) {
    Write-Host "Creating .env from .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "--------------------------------------------------------" -ForegroundColor Cyan
    Write-Host "A fresh '.env' file has been created at the project root." -ForegroundColor Green
    Write-Host "Please open '.env' and enter your SAS IC and Azure OpenAI credentials." -ForegroundColor Green
    Write-Host "After configuration, re-run this script to start the app." -ForegroundColor Green
    Write-Host "--------------------------------------------------------" -ForegroundColor Cyan
    Exit 0
}

# 3. Establish virtual environment
if (-not (Test-Path "venv")) {
    Write-Host "Virtual environment 'venv' not found. Creating..." -ForegroundColor Yellow
    python -m venv venv
    if (-not (Test-Path "venv")) {
        Write-Host "ERROR: Failed to create virtual environment." -ForegroundColor Red
        Exit 1
    }
    Write-Host "Virtual environment created successfully." -ForegroundColor Green
}

# 4. Activate virtual environment
Write-Host "Activating virtual environment..." -ForegroundColor Yellow
& .\venv\Scripts\Activate.ps1

# 5. Install / Update dependencies
Write-Host "Installing/updating dependencies from requirements.txt..." -ForegroundColor Yellow
python -m pip install --upgrade pip
pip install -r requirements.txt

# 6. Run application
Write-Host "Starting RMEDA Service..." -ForegroundColor Green
Write-Host "Open http://127.0.0.1:5000/ in your browser." -ForegroundColor Cyan
python app.py
