# Soundbar control without the SmartThings cloud API

This guide combines the existing **Soundbar Local (LAN)** entry with the new
**Soundbar advanced settings (Local OCF, own key)** entry. They are separate local
protocols. The advanced entry does not need a SmartThings PAT, OAuth token or
cloud account entry, and never falls back to a cloud command.

The OCF settings and credential-recovery method below were verified on an
**HW-Q990H**, using the iPhone which originally paired it. This is a verified
model, not a promise that every Samsung soundbar or firmware supports it.
The SmartThings phone app may still be needed for original onboarding; this is
cloud-free operation after setup, not cloud-free factory provisioning.

## What is implemented

| Control | Local route | Verification and limits |
|---|---|---|
| Power, overall volume, mute, input, sound mode | Existing LAN entry, HTTPS JSON-RPC on port 1516 | Existing integration support; confirm on your model before migrating |
| Voice Enhancement, Night Mode, Bass Boost | New authenticated OCF entry | Actual changes and readbacks verified on HW-Q990H |
| Active Voice Amplifier, SpaceFit Sound | OCF `/sec/networkaudio/av/soundsetting` | Changes and readbacks verified; these differ from Voice Enhancement |
| Rear speaker placement (Front/Rear) | OCF surround-speaker resource | Changes and readbacks verified |
| Woofer level (-12 to +6) | OCF woofer resource | Whole steps verified; allowed range can differ on another model |
| Center, Side, Wide, Front Top, Rear, Rear Top (-6 to +6) | OCF channel-volume resource | Changes and readbacks verified; inactive channels remain unavailable |
| Bass and Treble (-6 to +6) | OCF tone resource | Changes and readbacks verified; Bass can also alter woofer level |
| EQ preset | OCF EQ resource | Uses only advertised NONE/POP/JAZZ/CLASSIC/CUSTOM choices; CUSTOM selects the saved bank |
| Individual custom EQ frequency bands | Not implemented | Tested band writes were ignored; no working support claimed |
| Playback, track skipping, announcements | Not added by this change | Do not assume cloud `audioNotification` services become local; Cast is a separate integration with its own session limitations |

EQ preset changes can change the sound-processing mode. Bass/Treble may apply
outside Standard mode; they are not a substitute for editing the custom EQ bank.
An accepted response alone does not prove a command worked: the OCF client reads
the setting back, reports failures without optimistic state, and never resends an
ambiguous write. Setup and discovery send no setting, ACL, ownership or pairing
writes. All authenticated sessions are closed after each transaction.

## 1. Keep the existing basic LAN controls

Enable IP control in the SmartThings app if supported, reserve the soundbar's
address in DHCP, then add **Soundbar Local (LAN)** using that address. Confirm the
controls you need on your own model. The existing port-1516 connection and the
OCF connection below use different authentication; enabling IP control alone
does not supply an OCF key.

## 2. Recover your own OCF key

The verified route uses a **finished, unencrypted local iPhone backup**, created
with Apple Devices (Windows) or Finder (macOS), from the iPhone which originally
paired the soundbar. Recovery from a phone which only signed into the account
later has not been established. A backup contains broader personal information;
keep it on a trusted computer and do not upload it to this project.

The helper accepts an explicit backup directory. It queries only SmartThings'
`oic_svr_db_client.dat` entry in the backup index and reads only that app file.
It does not inspect Keychain, other apps or iCloud. It matches exactly one PSK to
the selected device, then verifies `/oic/d` and reads mapped settings over
authenticated DTLS before writing the private output. Keys and device IDs are
not printed. Nothing is reset, re-paired, or changed on the soundbar.

Use a separate Python 3.11+ virtual environment on your computer, **not a pip
install into Home Assistant Core or HAOS**:

```sh
python -m venv .venv
# macOS/Linux: .venv/bin/python; Windows: .venv\Scripts\python.exe
.venv/bin/python -m pip install smartthings-local==0.1.24
.venv/bin/python tools/recover_soundbar_ocf_key.py \
  --backup /private/path/to/one/finished/iphone-backup \
  --host 192.168.1.50 \
  --output /private/path/outside-this-checkout/credentials.json
```

Replace those paths and the example LAN address. On Windows, run the command on
one line with `.venv\Scripts\python.exe` and quote paths containing spaces.
The soundbar must be on and reachable; multicast OCF discovery must reach the
LAN. The helper discovers advertised dynamic ports, rather than scanning a port
range. Use the same LAN, or configure multicast routing deliberately if using
separate VLANs. Do not expose these services to the internet.

Successful output reports `verified_private_file_created`, a count of mapped
settings, and that no settings or pairing were changed. It refuses an existing
output file and refuses to create output inside this checkout. The file holds
`version`, `host`, `device_id`, `identity` and the base64 PSK `key`; do not post it.
It is created with mode 0600 on POSIX. On Windows, store it in a private directory
and restrict access using the directory's NTFS permissions.

If it reports `not_exported`, stop and check the guide's prerequisites. This
helper intentionally does not support encrypted backup indexes, `.datenc`
stores, certificate-only stores, missing/ambiguous keys, or an unverified peer.
It does not try a list of keys. Do not reset the soundbar just to make it pass.
An encrypted local backup may be preferable for general backup use, but is not
supported by this particular recovery tool. Android recovery is separate work;
the iOS instructions are not an Android recipe.

## 3. Add the advanced local entry

1. Securely copy **only** the generated file into
   `<HA configuration>/.storage/samsung_smartthings_ocf/credentials.json`.
   Create the directory privately (0700) and restrict the file to 0600 on HAOS.
   Avoid chat, public uploads, shared folders, shell arguments containing keys,
   and copying the entire phone backup to Home Assistant.
2. In Devices & services, add this integration and choose **Soundbar advanced
   settings (Local OCF, own key)**. Enter only `credentials.json`, not its contents
   or an absolute path. The flow reads from the fixed private directory.
3. Leave the soundbar on for the initial read-only check. Device identity is
   authenticated before any settings are read. A different device in the private
   file cannot silently replace a previously configured soundbar.
4. The new local device exposes five switches, two selects, and nine sliders.
   Controls absent from that model's response remain unavailable. A failed
   connection marks the entry unavailable and polling retries without cloud
   fallback. An off/unreachable soundbar can legitimately be unavailable.
5. Update automations and dashboards to use these new entities. Use their entity
   picker; entity IDs are generated per device rather than hard-coded in this
   guide. For EQ, only select the options actually advertised by your unit.

The credential file is **not encrypted at rest**. Treat Home Assistant backups
which include it as sensitive. Replacing a file with a new key for the same
device or changing its host requires reloading the OCF entry. Removing the entry
does not delete your credential file or phone backup; manage those deliberately.
No cloud credentials are copied into this file. No `.storage` credentials, phone
backups, real keys, private identifiers or household configuration belong in a PR.

## 4. Migrate away from cloud deliberately

Verify your local controls before disabling their cloud counterparts. Move
dashboard cards, scripts and automations to the local entities, then disable
unused cloud entries through Devices & services. Keep a rollback backup. No
automatic disabling, deletion, token revocation or device unpairing is performed
by this contribution.

**Do not uninstall this whole integration to remove cloud**: its package also
provides the local soundbar driver. Disable only the unwanted cloud config
entries. If you still use the cloud for another device, keep that account entry.

TV controls are separate. On the tested installation a local Samsung TV
WebSocket/REST integration provided app launch and remote keys, with Wake-on-LAN
for power-on. Audit that provider's optional SmartThings settings and cloud
credentials separately; a local soundbar does not automatically make a TV
integration local. Streaming services still require the internet for content.

## Validation and reporting

The shipped protocol mappings were exercised on HW-Q990H through a separate
local implementation, including real setting changes, readbacks and restoration.
The exact contribution passed 34 synthetic tests on Home Assistant's Linux
runtime. Its real HA classes import and construct sixteen distinct native
entities, and its exact protocol client completed an authenticated read of all
sixteen mapped settings on HW-Q990H. These checks ran in a separate process,
without installing this new entry or sending setting writes. They are not a
full installed-entry, end-to-end UI or multi-model test.
See `tests/test_soundbar_ocf.py` for runnable synthetic tests and their scope.

For useful reports, share model/firmware, failing feature, whether the basic LAN
entry still works, and a redacted description of the failure. Never attach the
credential JSON, backup, raw OCF security resource, full HA config or credentials.

Transport credit: [QuiteYellow/SmartThings-Local](https://github.com/QuiteYellow/SmartThings-Local)
(`smartthings-local` 0.1.24, MIT). This contribution uses its published transport,
PSK authentication and advertised-port discovery APIs; no third-party source or
Samsung fleet signing keys are bundled. The small soundbar discovery adapter
correlates known-host multicast responses. Apple documents local backups in
[Apple Devices](https://support.apple.com/guide/devices-windows/back-up-and-restore-your-device-mchla3c8ed03/windows).

AI disclosure: OpenAI Codex assisted with the implementation, tests and guide.
Examples and fixtures are synthetic. Device-specific evidence is described
without publishing the owner's keys, identities, network layout or configuration.
