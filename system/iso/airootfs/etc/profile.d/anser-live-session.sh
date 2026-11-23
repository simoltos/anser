if [ "$(id -un)" = live ] && [ "$(tty 2>/dev/null)" = /dev/tty1 ]; then
    exec uwsm start -g -1 -e -D Hyprland hyprland.desktop
fi
