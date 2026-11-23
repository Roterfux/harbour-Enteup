// Off-screen check of the grid's selection on the phone (qmlscene, minimal platform):
// the real CloudPage with a stand-in for the app, driven step by step. Prints STEP lines.
// The root is not visual, so qmlscene opens no window and needs no OpenGL; the page's
// logic and bindings run all the same (Silica warns about the missing ApplicationWindow).
import QtQuick 2.6
import Sailfish.Silica 1.0
import "../qml/pages"

QtObject {
    id: win

    property Item host: Item {
        width: 540
        height: 1200

        CloudPage {
            id: thePage
            app: win.fakeApp
            width: 540
            height: 1200
        }
        PreviewPage {
            id: preview
            app: win.fakeApp
            width: 540
            height: 1200
            cloudId: 908588095
            ids: [908588095, 908588265, 908588401]
            index: 0
        }
        FreeUpPage {
            id: freeUp
            app: win.fakeApp
            width: 540
            height: 1200
        }
    }

    property Item fakeApp: Item {
        property var cloud: ({ syncing: false, albums: 0, albumsDone: 0, fetched: 0, files: 3,
                               synced: 1, error: "", locked: 0 })
        property bool cloudStarted: true
        property var calls: []
        signal cloudSynced()
        signal photosRemoved()
        signal thumbReady(real thumbId, string thumbPath)
        signal trashedReady(real trashedId, var result)
        signal actedReady(var result)
        signal fullReady(real fullId, var result)
        signal savedReady(real savedId, var result)
        signal cleanupReady(int step, var result)
        property var thumbCache: ({})
        function call(name, args, callback) {
            calls.push(name + " " + JSON.stringify(args))
            console.log("STEP call " + name + " " + JSON.stringify(args))
            if (name === "cloud_rows" && callback)
                callback([{ t: "d", day: 1700000000, n: 3 },
                          { t: "r", h: 1, cells: "908588095:0:1:0:0:1:1,908588265:0:0:1:0:1:1" },
                          { t: "r", h: 2, cells: "908588401:1:0:0:0:3:2" },
                          { t: "d", day: 1690000000, n: 1 },
                          { t: "r", h: 1, cells: "596657031:0:0:0:0:1:1" }])
            if (name === "cloud_memory_covers" && callback)
                callback([{ kind: "months", ago: 1, day: 1700000000, n: 4, cover: 908588095 },
                          { kind: "best", ago: 2, day: 1690000000, n: 12, of: 80, cover: 596657031 }])
            if (name === "cloud_clip" && callback) {
                callback({ ok: true, reason: "" })
                fullReady(args[0], { video: "/tmp/eu-none.mp4" })
            }
        }
        function bytes(n) { return Math.round(n / 1024) + " KB" }
        function thumb(id) { return null }
        function cloudSync() {}
        function retryThumbs() {}
    }

    function cells(item, found) {
        found = found || []
        if (!item || found.indexOf(item) >= 0) return found
        if (item.fileId !== undefined && item.toggled !== undefined) found.push(item)
        var kids = item.children || []
        for (var i = 0; i < kids.length; ++i) cells(kids[i], found)
        if (item.contentItem && item.contentItem !== item) cells(item.contentItem, found)
        return found
    }

    function find(item, name, seen) {
        seen = seen || []
        if (!item || seen.indexOf(item) >= 0) return null
        seen.push(item)
        if (item.objectName === name) return item
        var kids = item.children || []
        for (var i = 0; i < kids.length; ++i) {
            var hit = find(kids[i], name, seen)
            if (hit) return hit
        }
        if (item.contentItem && item.contentItem !== item) return find(item.contentItem, name, seen)
        return null
    }

    // the pull-down menu's entries as the page has them: text and whether they show
    function menu(item, seen) {
        seen = seen || []
        if (!item || seen.indexOf(item) >= 0) return
        seen.push(item)
        if (item.text !== undefined && String(item).indexOf("MenuItem") >= 0)
            console.log("STEP menu entry '" + item.text + "' visible=" + item.visible
                        + " enabled=" + item.enabled)
        var kids = item.children || []
        for (var i = 0; i < kids.length; ++i) menu(kids[i], seen)
        if (item.contentItem && item.contentItem !== item) menu(item.contentItem, seen)
    }

    function report(page, label) {
        var marks = cells(page).map(function (c) {
            return c.fileId + (c.selected ? "*" : "") + (c.selecting ? "" : "(off)")
        })
        console.log("STEP " + label + ": selecting=" + page.selecting + " count="
                    + page.selectedCount + " cells=" + marks.join(" "))
    }

    property int step: 0
    property Timer driver: Timer {
        interval: 700
        repeat: true
        running: true
        onTriggered: {
            var page = thePage
            var all = win.cells(page)
            switch (win.step++) {
            case 0:
                var strip = win.find(page, "coverStrip")
                console.log("STEP covers: " + (strip ? "count=" + strip.count + " shown=" + strip.visible
                            + " h=" + Math.round(strip.height) + " titles="
                            + page.covers.map(function (c) { return page.coverTitle(c) + " / "
                                                             + page.coverText(c) }).join(" | ")
                            : "MISSING"))
                win.report(page, "loaded (" + all.length + " cells)")
                var button = win.find(page, "selectButton")
                console.log("STEP header button: " + (button ? "there, shown=" + button.visible
                                                           : "MISSING"))
                if (button) { button.clicked(null); win.report(page, "header button") }
                if (button) { button.clicked(null); win.report(page, "header button again") }
                break
            case 1: all[0].held(); win.report(page, "hold first"); break
            case 2: all[2].toggled(); win.report(page, "tap third"); break
            case 3: all[0].toggled(); win.report(page, "tap first again"); break
            case 4: page.toggleDay(3); win.report(page, "tap second day"); break
            case 5: page.toggleDay(0); win.report(page, "tap first day"); break
            case 6: page.trashSelected(); console.log("STEP trash asked"); break
            case 7:
                // big tiles: placed by x, y, w, h; the big video in view plays
                page.stopSelecting()
                page.pickClip()
                for (var k = 0; k < all.length; ++k)
                    console.log("STEP tile " + all[k].fileId + " at " + Math.round(all[k].x) + ","
                                + Math.round(all[k].y) + " size " + Math.round(all[k].width) + "x"
                                + Math.round(all[k].height) + " big=" + all[k].big
                                + " fav=" + all[k].favorite + " play=" + all[k].play
                                + " clip=" + all[k].clipPath)
                console.log("STEP clipId " + page.clipId + " cell " + Math.round(page.cell))
                break
            case 12: console.log("STEP after remorse: " + win.fakeApp.calls.join(" | ")); break
            case 13:
                win.fakeApp.trashedReady(0, { done: 4, skipped: 0, error: "" })
                win.report(page, "trashed")
                console.log("STEP notice: " + page.notice)
                break
            case 14:
                page.startSelecting(908588095)
                page.toggle(908588265)
                page.act("favorite", "cloud_favorite", [page.selectedIds(), true])
                win.fakeApp.actedReady({ what: "favorite", done: 2, skipped: 0, error: "" })
                win.report(page, "favorited")
                console.log("STEP notice: " + page.notice)
                page.startSelecting(908588401)
                page.act("save", "cloud_save_many", [page.selectedIds()])
                win.fakeApp.actedReady({ what: "save", done: 1, skipped: 0, error: "",
                                         folder: "Pictures/Ente" })
                console.log("STEP notice: " + page.notice + " | allIds=" + page.allIds.join(","))
                break
            case 15:
                console.log("STEP preview at " + preview.index + " id " + preview.cloudId)
                preview.go(1)
                break
            case 16:
                console.log("STEP preview swiped to " + preview.index + " id " + preview.cloudId
                            + " stage.x ok")
                preview.go(1)
                break
            case 17:
                console.log("STEP preview swiped to " + preview.index + " id " + preview.cloudId)
                preview.go(1)       // the last one: stays
                break
            case 18:
                console.log("STEP preview at end " + preview.index)
                preview.working = "favorite"
                win.fakeApp.actedReady({ what: "favorite", done: 1, skipped: 0, error: "" })
                console.log("STEP preview favorite=" + preview.favorite)
                console.log("STEP calls: " + win.fakeApp.calls.slice(-4).join(" | "))
                break
            case 19:
                // zoom: the point under the fingers stays where it is on the screen
                var flick = win.find(preview, "flick")
                flick.zoomTo(2, Qt.point(400, 900))     // pinch around (400, 900), still unzoomed
                console.log("STEP zoom 2 at (400,900): content " + flick.contentWidth + "x"
                            + flick.contentHeight + " scrolled to " + flick.contentX + ","
                            + flick.contentY + " -> point now at screen "
                            + (800 - flick.contentX) + "," + (1800 - flick.contentY))
                flick.zoomTo(1, Qt.point(0, 0))
                flick.returnToBounds()
                console.log("STEP zoom back: " + flick.contentWidth + "x" + flick.contentHeight
                            + " at " + flick.contentX + "," + flick.contentY + " zoom " + flick.zoom)
                break
            case 20:
                freeUp.check()
                win.fakeApp.cleanupReady(0, { progress: 1, total: 3 })
                console.log("STEP freeup checking " + freeUp.stage + " " + freeUp.progress + "/"
                            + freeUp.total)
                win.fakeApp.cleanupReady(0, { files: [
                    { path: "/a.jpg", name: "a.jpg", size: 2048, taken: 1700000000, album: "A", video: false },
                    { path: "/b.mp4", name: "b.mp4", size: 4096, taken: 1700000001, album: "B", video: true }],
                    bytes: 6144, checked: 3, notInEnte: 1, error: "" })
                console.log("STEP freeup found " + freeUp.files.length + " chosen "
                            + freeUp.chosenCount + " size " + freeUp.chosenSize)
                freeUp.skip["/b.mp4"] = true
                freeUp.skipRevision += 1
                console.log("STEP freeup after keep: chosen " + freeUp.chosenCount + " size "
                            + freeUp.chosenSize)
                freeUp.remove()
                break
            case 30:
                console.log("STEP freeup asked: " + win.fakeApp.calls.slice(-1)[0])
                win.fakeApp.cleanupReady(1, { done: 1, freed: 2048, kept: 0, error: "" })
                console.log("STEP freeup notice: " + freeUp.notice + " | left "
                            + freeUp.files.length)
                break
            case 31: console.log("STEP DONE"); Qt.quit(); break
            }
        }
    }
}
