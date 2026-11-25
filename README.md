# Anser Linux

Anser is an [Omarchy](https://omarchy.org/) flavoured distro. Stock Omarchy is the quickest way to get started.
I'm building Anser for myself so I can boot straight into my everyday setup after installation.

## The iso builder

I started with Omarchy's ISO builder. The Anser builder and my customized installer are both in `iso/`.
x86_64 is the main target. The ARM64 ISO boots in QEMU, but it's still experimental since I don't have
an ARM machine to try it on. More details in the [builder README](iso/README.md).

## The system

The basic system is under `system/`. For now it's vanilla Omarchy 4.5. This is where I'll make my own tweaks.

## Download

You can download the [x86_64 ISO](https://anser-download.simoltos.workers.dev/latest) and
[its signature](https://anser-download.simoltos.workers.dev/latest.sig).
