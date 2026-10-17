# Administrator verification for a shared music library

Listeners can continue browsing, playing, using Album Radio, favorites and
playlists with the existing listening password or API key. Those credentials
do not authorize management operations.

On the server PC, sign in at `http://localhost:8765`, open **Settings →
Administrator protection**, and set a separate administrator password. The
listening password must already be configured in Remote Access. The two
passwords must differ; share only the listening password with friends.

Management stays locked until setup is complete. First administrator setup is
accepted only through a direct localhost request, with ordinary authentication.
Tailnet/proxy requests and cross-origin requests cannot claim initial ownership.
Changing the administrator password later requires the current administrator
password.

Every sensitive request asks for the administrator password again. There is no
persistent unlocked browser session. Cancelling or entering a wrong password
does not perform the operation; repeated guesses are limited to five failures
per client in five minutes. Administrator lockout does not prevent listening.

Protected actions include:

- Applying a searched cover, Auto Cover and custom cover upload.
- Opening a server folder, re-reading album/artist tags, deleting or archiving
  an album and restoring archived files.
- Library configuration, scans, reset, enrichment and thumbnail maintenance.
- Shared DSP profile changes, DLNA access grants, CORS changes and pool recycling.
- Changing the listening password, disabling listening authentication, and
  rotating API keys, including the legacy GET reset endpoint.

Cover search and playback remain available without administrator verification;
applying a chosen cover requires approval. All checks run on the server, even
when requests originate from localhost or LAN authentication bypass is enabled.
Listening keys, image cookies, URL parameters and media tickets do not replace
the administrator password. The existing listening authentication is still
required before administrator verification.

The administrator password is never saved in browser storage. Only its salted
PBKDF2-HMAC-SHA256 verifier (600,000 iterations) is stored in
`admin-security.json`, next to the existing `security.json`. Keeping a separate
file preserves compatibility of the listening configuration. Protect that data
directory as you already protect the listening credentials. Use Tailnet HTTPS
for remote access.

The preview uses an isolated library/configuration snapshot. Setting a password
there affects the preview; configure the administrator password in the actual
server environment when deploying this branch. Older application branches do
not enforce this new gate.
