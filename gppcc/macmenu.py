"""macOS menu-bar icon (Open settings / Pause / Quit).

SDL already runs the Cocoa event loop on the main thread every time the engine
polls the gamepad, so the status item is serviced without a separate run loop.
"""

from __future__ import annotations

import webbrowser

import objc
from AppKit import (
    NSApplication,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSObject,
    NSStatusBar,
    NSVariableStatusItemLength,
)

from . import output


class _MenuController(NSObject):
    def initWithEngine_url_(self, engine, url):
        self = objc.super(_MenuController, self).init()
        if self is None:
            return None
        self.engine = engine
        self.url = url
        return self

    # -- actions -----------------------------------------------------------
    def openUI_(self, sender):
        webbrowser.open(self.url)

    def togglePause_(self, sender):
        self.engine.set_enabled(not self.engine.enabled)

    def openAccessibility_(self, sender):
        output.request_permissions()
        webbrowser.open("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")

    def quit_(self, sender):
        self.engine.running = False

    # -- NSMenuDelegate: refresh titles right before the menu opens ----------
    def menuNeedsUpdate_(self, menu):
        e = self.engine
        info = e.pads.info()
        self.status_item.setTitle_(f"🎮 {info['name']}" if info["connected"] else "No gamepad connected")
        self.profile_item.setTitle_(f"Profile: {e.active_name}")
        self.pause_item.setTitle_("Resume control" if not e.enabled else "Pause control")
        self.perm_item.setHidden_(output.has_permissions())


class MenuBar:
    def __init__(self, engine, url: str):
        NSApplication.sharedApplication()
        self.ctrl = _MenuController.alloc().initWithEngine_url_(engine, url)

        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        img = None
        if hasattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_"):
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_("gamecontroller", "gppcc")
        if img is not None:
            img.setTemplate_(True)
            self.item.button().setImage_(img)
        else:
            self.item.button().setTitle_("🎮")

        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        menu.setDelegate_(self.ctrl)

        def add(title, action=None, key=""):
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            if action:
                it.setTarget_(self.ctrl)
            else:
                it.setEnabled_(False)
            menu.addItem_(it)
            return it

        self.ctrl.status_item = add("gppcc")
        self.ctrl.profile_item = add("Profile")
        menu.addItem_(NSMenuItem.separatorItem())
        add("Open settings…", "openUI:", ",")
        self.ctrl.pause_item = add("Pause control", "togglePause:", "p")
        self.ctrl.perm_item = add("⚠ Grant Accessibility permission…", "openAccessibility:")
        menu.addItem_(NSMenuItem.separatorItem())
        add("Quit gppcc", "quit:", "q")

        self.item.setMenu_(menu)
        self.menu = menu  # keep references alive
