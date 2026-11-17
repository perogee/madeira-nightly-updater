# Madeira AltStore / SideStore source

Auto-updating source for [camhoc/Madeira](https://github.com/camhoc/Madeira) builds,
served with GitHub Pages.

## Source URL

```
https://<your-username>.github.io/<this-repo>/source.json
```

Or open `https://<your-username>.github.io/<this-repo>/` on your iPhone and tap
**Add to AltStore** / **Add to SideStore**.

## Setup

1. **Settings → Pages → Build and deployment → Source:** choose **GitHub Actions**.
2. **Settings → Actions → General → Workflow permissions:** choose **Read and write**.
3. **Actions → Update source → Run workflow** once to publish the site.

## How it updates

Every 3 hours the workflow checks camhoc/Madeira's releases, downloads any new
IPA, reads its real version/build/bundle ID, commits the new entry to
`source.json`, and redeploys the Pages site. The newest 10 builds are kept.
Pushing your own edits to `main` also redeploys.

To track a different fork, change `UPSTREAM_REPO` in the workflow.
