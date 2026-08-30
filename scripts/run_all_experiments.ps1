Write-Host "Running comprehensive fault injection campaign..."

$experiments = @(
    # CPU Faults
    @{ container = "user-service"; type = "cpu" },
    @{ container = "social-graph-service"; type = "cpu" },
    @{ container = "post-storage-service"; type = "cpu" },
    
    # Memory Faults
    @{ container = "home-timeline-service"; type = "memory" },
    @{ container = "user-timeline-service"; type = "memory" },
    
    # Restart Faults
    @{ container = "media-service"; type = "restart" },
    @{ container = "text-service"; type = "restart" }
)

foreach ($exp in $experiments) {
    $c = $exp.container
    $t = $exp.type
    Write-Host "`n>>> Starting $t fault on $c <<<" -ForegroundColor Cyan
    python scripts/run_fault_experiment.py --container $c --type $t
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Experiment failed, but continuing to next..." -ForegroundColor Red
    }
}

Write-Host "`n>>> Building RCA Dataset <<<" -ForegroundColor Green
python scripts/build_rca_dataset.py
Write-Host "Done!"
