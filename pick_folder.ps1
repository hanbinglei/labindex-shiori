Add-Type -AssemblyName Microsoft.VisualBasic
Add-Type -AssemblyName System.Windows.Forms

$userPath = [Microsoft.VisualBasic.Interaction]::InputBox(
    "Enter a folder path to scan, or leave empty to browse:",
    "Select Folder",
    "\\YOUR_NAS_SERVER\Research_Papers")

if ([string]::IsNullOrWhiteSpace($userPath)) {
    # User left empty or cancelled -> open graphical folder picker
    $f = New-Object System.Windows.Forms.FolderBrowserDialog
    $f.Description = "Select a folder to scan"
    $f.SelectedPath = "\\YOUR_NAS_SERVER\Research_Papers"
    $f.ShowNewFolderButton = $false
    $r = $f.ShowDialog()
    if ($r -eq [System.Windows.Forms.DialogResult]::OK) {
        Write-Output $f.SelectedPath
    }
    # else: cancelled, exit 1 (no output -> batch detects empty)
} else {
    Write-Output $userPath
}
