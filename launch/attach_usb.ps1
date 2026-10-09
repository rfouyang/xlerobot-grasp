# Windows (administrator PowerShell): the robot's USB devices into WSL after a reboot. Bus ids change between boots,
# so devices are found by hardware id: the two motor buses and the two wrist cameras first, the RealSense last (it
# sometimes fails with "Set Address Failed" and then needs a physical replug).
$robot = @("1a86:55d3", "0c45:64ab")
$realsense = "8086:0ad3"
$list = usbipd list
function Attach($hwid) {
    $rows = $list | Select-String -Pattern ("^\s*(\S+)\s+" + [regex]::Escape($hwid))
    if (-not $rows) { Write-Host "not found: $hwid"; return }
    foreach ($row in $rows) {
        $busid = $row.Matches[0].Groups[1].Value
        usbipd bind --busid $busid 2>$null
        usbipd attach --wsl --busid $busid
        Write-Host "attached $hwid at $busid"
        Start-Sleep -Seconds 1
    }
}
foreach ($hwid in $robot) { Attach $hwid }
Start-Sleep -Seconds 2
Attach $realsense
usbipd list
