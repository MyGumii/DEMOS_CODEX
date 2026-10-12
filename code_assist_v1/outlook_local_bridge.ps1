param(
    [ValidateSet("status", "fetch", "unread")]
    [string]$Mode = "status",
    [string]$SinceUtc = "",
    [int]$Limit = 100
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$Limit = [Math]::Max(1, [Math]::Min(500, $Limit))

function Release-ComObject {
    param([object]$Value)
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($Value)
    }
}

function Get-OutlookApplication {
    try {
        return [Runtime.InteropServices.Marshal]::GetActiveObject("Outlook.Application")
    } catch {
        return New-Object -ComObject Outlook.Application
    }
}

function Get-PropertyValue {
    param([object]$Item, [string]$Schema)
    try {
        return [string]$Item.PropertyAccessor.GetProperty($Schema)
    } catch {
        return ""
    }
}

function Get-SenderAddress {
    param([object]$Item)
    $address = ""
    try { $address = [string]$Item.SenderEmailAddress } catch {}
    if ($address -and -not $address.StartsWith("/O=", [StringComparison]::OrdinalIgnoreCase)) {
        return $address
    }
    $smtp = Get-PropertyValue $Item "http://schemas.microsoft.com/mapi/proptag/0x39FE001E"
    if ($smtp) { return $smtp }
    try {
        $sender = $Item.Sender
        if ($null -ne $sender) {
            $exchangeUser = $sender.GetExchangeUser()
            if ($null -ne $exchangeUser -and $exchangeUser.PrimarySmtpAddress) {
                $smtp = [string]$exchangeUser.PrimarySmtpAddress
            }
            Release-ComObject $exchangeUser
        }
        Release-ComObject $sender
    } catch {}
    if ($smtp) { return $smtp }
    return $address
}

function Convert-MailItem {
    param([object]$Item)

    $received = $null
    $modified = $null
    try { $received = ([datetime]$Item.ReceivedTime).ToUniversalTime().ToString("o") } catch {}
    try { $modified = ([datetime]$Item.LastModificationTime).ToUniversalTime().ToString("o") } catch {}
    $body = ""
    try { $body = [string]$Item.Body } catch {}
    if ($body.Length -gt 50000) { $body = $body.Substring(0, 50000) }

    $attachments = @()
    $attachmentCollection = $null
    try {
        $attachmentCollection = $Item.Attachments
        $attachmentCount = [Math]::Min([int]$attachmentCollection.Count, 50)
        for ($index = 1; $index -le $attachmentCount; $index++) {
            $attachment = $null
            try {
                $attachment = $attachmentCollection.Item($index)
                $attachments += [ordered]@{
                    name = [string]$attachment.FileName
                    size = [int64]$attachment.Size
                }
            } catch {
            } finally {
                Release-ComObject $attachment
            }
        }
    } catch {}
    Release-ComObject $attachmentCollection

    $entryId = ""
    $conversationId = ""
    $subject = ""
    $senderName = ""
    $to = ""
    $cc = ""
    $unread = $false
    $importance = 1
    try { $entryId = [string]$Item.EntryID } catch {}
    try { $conversationId = [string]$Item.ConversationID } catch {}
    try { $subject = [string]$Item.Subject } catch {}
    try { $senderName = [string]$Item.SenderName } catch {}
    try { $to = [string]$Item.To } catch {}
    try { $cc = [string]$Item.CC } catch {}
    try { $unread = [bool]$Item.UnRead } catch {}
    try { $importance = [int]$Item.Importance } catch {}

    return [ordered]@{
        entry_id = $entryId
        internet_message_id = Get-PropertyValue $Item "http://schemas.microsoft.com/mapi/proptag/0x1035001E"
        conversation_id = $conversationId
        subject = $subject
        sender_name = $senderName
        sender_address = Get-SenderAddress $Item
        to = $to
        cc = $cc
        received_at = $received
        modified_at = $modified
        unread = $unread
        importance = $importance
        body = $body
        attachments = @($attachments)
    }
}

$outlook = $null
$namespace = $null
$inbox = $null
$items = $null
try {
    $outlook = Get-OutlookApplication
    $namespace = $outlook.GetNamespace("MAPI")
    $inbox = $namespace.GetDefaultFolder(6)

    if ($Mode -eq "status") {
        $unreadCount = 0
        try { $unreadCount = [int]$inbox.UnReadItemCount } catch {}
        $accountCount = 0
        try { $accountCount = [int]$namespace.Accounts.Count } catch {}
        $profileName = ""
        try { $profileName = [string]$namespace.CurrentProfileName } catch {}
        [ordered]@{
            available = $true
            connector = "classic_outlook"
            profile_name = $profileName
            account_count = $accountCount
            inbox_count = [int]$inbox.Items.Count
            unread_count = $unreadCount
        } | ConvertTo-Json -Compress -Depth 5
        exit 0
    }

    $items = $inbox.Items
    if ($Mode -eq "unread") {
        $restricted = $items.Restrict("[UnRead] = true")
        Release-ComObject $items
        $items = $restricted
    }
    $items.Sort("[ReceivedTime]", $true)

    $since = $null
    if ($SinceUtc) {
        try { $since = [datetime]::Parse($SinceUtc).ToUniversalTime() } catch {}
    }
    $messages = @()
    $count = [int]$items.Count
    for ($index = 1; $index -le $count -and $messages.Count -lt $Limit; $index++) {
        $item = $null
        try {
            $item = $items.Item($index)
            $messageClass = ""
            try { $messageClass = [string]$item.MessageClass } catch {}
            if (-not $messageClass.StartsWith("IPM.Note", [StringComparison]::OrdinalIgnoreCase)) { continue }
            if ($null -ne $since) {
                $receivedTime = ([datetime]$item.ReceivedTime).ToUniversalTime()
                if ($receivedTime -lt $since) { break }
            }
            $messages += Convert-MailItem $item
        } catch {
        } finally {
            Release-ComObject $item
        }
    }

    [ordered]@{
        available = $true
        connector = "classic_outlook"
        mode = $Mode
        total_count = $count
        messages = @($messages)
    } | ConvertTo-Json -Compress -Depth 7
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    Release-ComObject $items
    Release-ComObject $inbox
    Release-ComObject $namespace
    Release-ComObject $outlook
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
