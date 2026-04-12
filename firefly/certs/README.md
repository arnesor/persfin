# About the certs directory

This directory holds the TLS certificates used by Caddy to serve Firefly III and the
data importer over HTTPS locally.

It is also used by the Python programs to establish a secure https connection to [Enable Banking]. 

The `Caddyfile` expects these two files:

```
certs/
├── localhost+2.pem        ← certificate (public)
└── localhost+2-key.pem    ← private key
```

These files are git-ignored and must be generated locally.

[Enable Banking]: (https://www.enablebanking.com)
