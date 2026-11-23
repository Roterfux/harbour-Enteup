// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

// The photos in the Ente account as a grid by the day taken:
//   mode ""           all of them (the timeline, without hidden and archived photos, as in
//                     Ente's apps), or one album's (albumId)
//   mode "memories"   1 month, 3 months, half a year ago; this day in earlier years; the
//                     best of this month in earlier years
//   mode "duplicates" photos stored more than once, grouped (the copy that stays first)
//   mode "trash"      Ente's trash
// A few photos of a day show as big tiles (videos 3 x 2 or 2 x 3 by their shape); the
// topmost big video wholly in view plays silently.
// Selection: the button at the top left, "Select" in the pull-down menu, or press and hold
// a photo; tap more photos or a day's header; then act on them with the bar at the bottom.
Page {
    id: page
    property var app
    property string mode: ""
    // 0 = the timeline. Not int: Ente's album ids need more than 32 bits
    property real albumId: 0
    property string albumType: ""       // "favorites" for Ente's favorites album
    property bool shared: false         // an album shared with the user: only saving
    property string title: mode === "memories" ? qsTr("Memories")
                         : mode === "duplicates" ? qsTr("Duplicates")
                         : mode === "trash" ? qsTr("Ente's trash") : qsTr("Photos in Ente")
    property real clipId: 0             // the big video tile playing now
    property int memoryIndex: -1        // mode "memories": only this one (-1 = all)
    property var covers: []             // the timeline: memories as title pictures on top
    readonly property bool timeline: mode === "" && albumId === 0
    property var rows: []
    property var allIds: []             // the photos in grid order (for swiping)
    property bool loaded: false

    property bool selecting: false
    property var selected: ({})         // file id -> true
    property int selectedCount: 0
    property int selectionRevision: 0   // goes up with every change: cells look again
    property string working: ""         // the action running on the selection
    property string targetName: ""      // the album photos go to
    property string notice: ""

    readonly property bool trash: mode === "trash"
    readonly property bool dups: mode === "duplicates"
    readonly property bool mine: !shared && !trash && !dups

    allowedOrientations: Orientation.All
    backNavigation: !selecting
    showNavigationIndicator: !selecting

    readonly property int gap: Math.max(2, Math.round(Theme.paddingSmall / 3))
    readonly property int columns: Math.max(3, Math.round(width / (Screen.width / 4)))
    readonly property real cell: (width - (columns - 1) * gap) / columns

    function load() {
        var call = trash ? "cloud_trash_rows" : mode === "memories" ? "cloud_memories"
                 : dups ? "cloud_duplicates" : "cloud_rows"
        var args = mode === "memories" ? [columns, memoryIndex]
                 : mode !== "" ? [columns] : [albumId, columns]
        if (timeline)
            app.call("cloud_memory_covers", [], function (found) { page.covers = found })
        app.call(call, args, function (found) {
            page.rows = found
            var ids = []
            for (var i = 0; i < found.length; ++i) {
                if (found[i].t !== "r") continue
                var cells = found[i].cells.split(",")
                for (var j = 0; j < cells.length; ++j) ids.push(parseInt(cells[j].split(":")[0]))
            }
            page.allIds = ids
            page.loaded = true
            clipTimer.restart()
        })
    }

    // The big video tile to play: the topmost one wholly in view (one at a time)
    function pickClip() {
        var top = view.contentY, bottom = view.contentY + view.height
        var step = page.cell + page.gap
        var best = 0
        for (var y = top; y < bottom && best === 0; y += step / 2) {
            var row = view.itemAt(0, y)
            if (!row || !row.clips) continue
            for (var i = 0; i < row.clips.length; ++i) {
                var c = row.clips[i]
                var start = row.y + c.y * step
                if (start >= top - 1 && start + c.h * step - page.gap <= bottom + 1) {
                    best = c.id
                    break
                }
            }
        }
        clipId = best
    }
    Timer {
        id: clipTimer
        interval: 350           // once the grid stops moving
        onTriggered: page.pickClip()
    }

    // Duplicates: select every copy but the first (the one that stays) of each group
    function selectExtras() {
        if (!selecting) startSelecting(0)
        for (var i = 0; i < rows.length; ++i) {
            if (rows[i].t !== "d") continue
            var ids = dayIds(i)
            for (var j = 1; j < ids.length; ++j) {
                if (!selected[ids[j]]) { selected[ids[j]] = true; selectedCount += 1 }
            }
        }
        selectionRevision += 1
    }

    function openFavorites() {
        app.call("cloud_favorites", [], function (found) {
            if (!found.id) { page.notice = qsTr("No favorites yet: tap the heart on a photo"); return }
            pageStack.push(Qt.resolvedUrl("CloudPage.qml"),
                           { app: page.app, albumId: found.id, albumType: "favorites",
                             title: qsTr("Favorites") })
        })
    }

    function readTrash() {
        working = "trashsync"
        app.call("cloud_trash_sync")
    }

    function startSelecting(fileId) {
        selected = ({})
        selectedCount = 0
        selecting = true
        notice = ""
        if (fileId) toggle(fileId)
        selectionRevision += 1
    }

    function stopSelecting() {
        selecting = false
        selected = ({})
        selectedCount = 0
        selectionRevision += 1
    }

    function toggle(fileId) {
        if (selected[fileId]) {
            delete selected[fileId]
            selectedCount -= 1
        } else {
            selected[fileId] = true
            selectedCount += 1
        }
        selectionRevision += 1
    }

    // The ids of the day whose header is at index: in the rows after it, up to the next one
    function dayIds(index) {
        var ids = []
        for (var i = index + 1; i < rows.length && rows[i].t === "r"; ++i) {
            var cells = rows[i].cells.split(",")
            for (var j = 0; j < cells.length; ++j) ids.push(parseInt(cells[j].split(":")[0]))
        }
        return ids
    }

    // A day's header: select the whole day, or clear it when all of it is selected
    function toggleDay(index) {
        var ids = dayIds(index)
        var all = true
        for (var i = 0; i < ids.length; ++i) if (!selected[ids[i]]) all = false
        for (i = 0; i < ids.length; ++i) {
            if (all && selected[ids[i]]) { delete selected[ids[i]]; selectedCount -= 1 }
            else if (!all && !selected[ids[i]]) { selected[ids[i]] = true; selectedCount += 1 }
        }
        selectionRevision += 1
    }

    function selectedIds() {
        var ids = []
        for (var key in selected) ids.push(parseFloat(key))
        return ids
    }

    // Run an action of the engine on the selection; the answer comes as actedReady
    // (or trashedReady for the trash)
    function act(what, call, args) {
        working = what
        notice = ""
        app.call(call, args)
    }

    function trashSelected() {
        var ids = selectedIds()
        if (ids.length === 0) return
        remorse.execute(qsTr("Moving %n photo(s) to Ente's trash", "", ids.length), function () {
            page.act("trash", "cloud_trash_many", [ids])
        })
    }

    function toAlbum(moving) {
        var ids = selectedIds()
        if (ids.length === 0) return
        var picker = pageStack.push(Qt.resolvedUrl("AlbumsPage.qml"),
                                    { app: page.app, pick: true,
                                      pickTitle: moving ? qsTr("Move to album")
                                                        : qsTr("Add to album") })
        picker.picked.connect(function (albumId, name) {
            page.targetName = name
            if (moving) page.act("move", "cloud_move", [ids, page.albumId, albumId])
            else page.act("add", "cloud_add", [ids, albumId])
        })
    }

    function report(result) {
        var n = result.done || 0
        var parts = []
        var what = result.what || page.working
        if (what === "add") parts.push(qsTr("%n added to %1", "", n).arg(page.targetName))
        else if (what === "move") parts.push(qsTr("%n moved to %1", "", n).arg(page.targetName))
        else if (what === "favorite") parts.push(qsTr("%n added to favorites", "", n))
        else if (what === "unfavorite") parts.push(qsTr("%n removed from favorites", "", n))
        else if (what === "save") parts.push(qsTr("%n saved to %1", "", n).arg(result.folder || ""))
        else if (what === "restore") parts.push(qsTr("%n restored", "", n))
        else if (what === "delete") parts.push(qsTr("%n deleted for good", "", n))
        else if (what === "empty") parts.push(qsTr("Trash emptied (%n photo(s))", "", n))
        else if (what === "trash") parts.push(qsTr("%n moved to Ente's trash", "", n))
        else if (what === "dedupe")
            parts.push(qsTr("%n extra cop(ies) moved to Ente's trash; the copies that stay "
                            + "are in all their albums", "", n))
        if (result.skipped > 0)
            parts.push(qsTr("%n left alone (not yours, or already there)", "", result.skipped))
        if (result.error) parts.push(result.error)
        return parts.join(". ")
    }

    onColumnsChanged: if (loaded) load()
    Component.onCompleted: {
        load()
        if (trash) readTrash()
        else if (albumId === 0 && mode === "" && !app.cloudStarted) app.cloudSync()
    }
    Connections {
        target: page.app
        onCloudSynced: if (!page.trash) page.load()
        onPhotosRemoved: page.load()
        onTrashedReady: if (trashedId === 0 && page.working === "trash") {
            page.notice = page.report(result)       // before "working" is cleared: it names the action
            page.working = ""
            page.stopSelecting()
        }
        onActedReady: {
            if (page.working === "") return
            if (result.what === "trashsync") {
                if (page.working !== "trashsync") return
                page.working = ""
                page.notice = result.error ? result.error
                    : result.unreadable > 0
                      ? qsTr("%n photo(s) in the trash cannot be shown: their album is gone",
                             "", result.unreadable) : ""
                page.load()
                return
            }
            var mineNow = page.working === result.what
                          || (page.working === "favorite" && result.what === "unfavorite")
            if (!mineNow) return
            page.working = ""
            page.stopSelecting()
            page.notice = page.report(result)
            if (page.trash) page.load()
        }
    }

    RemorsePopup { id: remorse }

    function syncText() {
        if (trash) return working === "trashsync" ? qsTr("Reading the trash")
                                                 : qsTr("Ente deletes them for good after 30 days")
        var c = app.cloud
        if (c.syncing) {
            return c.albums > 0 ? qsTr("Updating: album %1 of %2, %3 photos read")
                                      .arg(Math.min(c.albumsDone + 1, c.albums)).arg(c.albums)
                                      .arg(c.fetched)
                                : qsTr("Updating")
        }
        if (c.error !== "") return c.error
        return c.synced > 0 ? qsTr("Updated %1").arg(Qt.formatDateTime(new Date(c.synced * 1000),
                                                                     "d MMM, hh:mm"))
                            : ""
    }

    // A memory's title picture: what it is, then when and how many
    // the app has no translations yet, so qsTr's %n would show "(s)" as it is
    function plural(n, one, many) { return (n === 1 ? one : many).arg(n) }
    function coverTitle(row) {
        if (row.kind === "best")
            return qsTr("Best of %1").arg(Qt.formatDate(new Date(row.day * 1000), "MMMM yyyy"))
        if (row.kind === "months")
            return row.ago === 6 ? qsTr("Half a year ago")
                                 : plural(row.ago, qsTr("%1 month ago"), qsTr("%1 months ago"))
        return plural(row.ago, qsTr("%1 year ago"), qsTr("%1 years ago"))
    }
    function coverText(row) {
        var count = plural(row.kind === "best" ? row.of : row.n, qsTr("%1 photo"), qsTr("%1 photos"))
        if (row.kind === "best") return count
        return Qt.formatDate(new Date(row.day * 1000), "d MMM yyyy") + "  ·  " + count
    }
    function openMemory(index) {
        pageStack.push(Qt.resolvedUrl("CloudPage.qml"),
                       { app: page.app, mode: "memories", memoryIndex: index,
                         title: coverTitle(covers[index]) })
    }

    function dayText(row) {
        var when = new Date(row.day * 1000)
        var date = Qt.formatDate(when, Qt.DefaultLocaleLongDate)
        if (row.kind === "dup")
            return (row.title !== "" ? row.title + "  ·  " : "")
                   + qsTr("%n copies", "", row.n) + "  ·  " + app.bytes(row.size)
        if (row.kind === "best")
            return qsTr("Best of %1").arg(Qt.formatDate(when, "MMMM yyyy")) + "  ·  "
                   + (row.n < row.of ? qsTr("%1 of %2").arg(row.n).arg(row.of) : row.n)
        if (row.ago !== undefined)
            date = coverTitle(row) + "  ·  " + date
        return date + "  ·  " + row.n
    }

    SilicaListView {
        id: view
        anchors { left: parent.left; right: parent.right; top: parent.top
                  bottom: selectionBar.visible ? selectionBar.top : parent.bottom }
        clip: selectionBar.visible
        model: page.rows
        cacheBuffer: page.height        // thumbnails a screen ahead are asked for already
        onContentYChanged: clipTimer.restart()
        onHeightChanged: clipTimer.restart()

        PullDownMenu {
            busy: app.cloud.syncing || page.working !== ""
            enabled: page.working === ""
            MenuItem {
                visible: page.albumId === 0 && page.mode === "" && !page.selecting
                text: qsTr("Albums")
                onClicked: pageStack.push(Qt.resolvedUrl("CloudAlbumsPage.qml"), { app: page.app })
            }
            MenuItem {
                visible: page.albumId === 0 && page.mode === "" && !page.selecting
                text: qsTr("Favorites")
                onClicked: page.openFavorites()
            }
            MenuItem {
                visible: page.albumId === 0 && page.mode === "" && !page.selecting
                text: qsTr("Memories")
                onClicked: pageStack.push(Qt.resolvedUrl("CloudPage.qml"),
                                          { app: page.app, mode: "memories" })
            }
            MenuItem {
                visible: page.albumId === 0 && page.mode === "" && !page.selecting
                text: qsTr("Duplicates")
                onClicked: pageStack.push(Qt.resolvedUrl("CloudPage.qml"),
                                          { app: page.app, mode: "duplicates" })
            }
            MenuItem {
                visible: page.dups && view.count > 0
                text: qsTr("Select extra copies")
                onClicked: page.selectExtras()
            }
            MenuItem {
                visible: page.albumId === 0 && page.mode === "" && !page.selecting
                text: qsTr("Trash")
                onClicked: pageStack.push(Qt.resolvedUrl("CloudPage.qml"),
                                          { app: page.app, mode: "trash" })
            }
            MenuItem {
                visible: page.trash && !page.selecting && view.count > 0
                text: qsTr("Empty trash")
                onClicked: remorse.execute(qsTr("Emptying Ente's trash: deleted for good"),
                                           function () { page.act("empty", "cloud_empty_trash", []) })
            }
            MenuItem {
                visible: view.count > 0
                text: page.selecting ? qsTr("Stop selecting") : qsTr("Select")
                onClicked: page.selecting ? page.stopSelecting() : page.startSelecting(0)
            }
            MenuItem {
                visible: !page.selecting
                text: qsTr("Load thumbnails again")
                onClicked: {
                    app.retryThumbs()
                    page.rows = []
                    page.load()
                }
            }
            MenuItem {
                visible: !page.selecting
                enabled: !app.cloud.syncing
                text: qsTr("Update")
                onClicked: page.trash ? page.readTrash() : app.cloudSync()
            }
        }

        header: Column {
            width: view.width
            Item {
                width: parent.width
                height: pageHeader.height
                PageHeader {
                    id: pageHeader
                    width: parent.width
                    title: page.selecting ? qsTr("%n selected", "", page.selectedCount) : page.title
                    description: page.selecting
                                 ? (page.dups ? qsTr("Tap the copies to remove, or a group")
                                              : qsTr("Tap photos, or a day to take all of it"))
                                 : page.dups ? qsTr("The first of each group stays")
                                 : page.syncText()
                }
                // Selection at hand, not only in the pull-down menu or by holding a photo
                IconButton {
                    id: selectButton
                    objectName: "selectButton"
                    anchors { left: parent.left; leftMargin: Theme.paddingMedium
                              verticalCenter: parent.verticalCenter }
                    visible: view.count > 0
                    icon.source: "image://theme/icon-m-select-all"
                    highlighted: page.selecting
                    onClicked: page.selecting ? page.stopSelecting() : page.startSelecting(0)
                }
            }
            Label {
                x: Theme.horizontalPageMargin
                width: parent.width - 2 * x
                visible: text !== ""
                wrapMode: Text.Wrap
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.highlightColor
                text: page.notice
            }
            // Memories as title pictures, side by side; tap one for its photos
            SilicaListView {
                id: coverStrip
                objectName: "coverStrip"
                width: parent.width
                readonly property bool wanted: page.timeline && page.covers.length > 0
                                               && !page.selecting
                height: wanted ? Math.round(page.cell * 2.2) + Theme.paddingMedium : 0
                visible: wanted
                orientation: ListView.Horizontal
                spacing: page.gap * 2
                leftMargin: Theme.horizontalPageMargin
                rightMargin: Theme.horizontalPageMargin
                clip: true
                model: page.covers
                delegate: MemoryCover {
                    app: page.app
                    width: Math.round(page.cell * 1.55)
                    height: coverStrip.height - Theme.paddingMedium
                    fileId: modelData.cover
                    title: page.coverTitle(modelData)
                    subtitle: page.coverText(modelData)
                    onClicked: page.openMemory(index)
                }
            }
        }

        delegate: Item {
            id: band
            width: view.width
            height: modelData.t === "d" ? dayLabel.height + Theme.paddingMedium
                                        : (modelData.h || 1) * (page.cell + page.gap)
            // id:kind:favorite:x:y:w:h per cell (x, y, w, h in cells within this band)
            readonly property var cells: modelData.t === "r"
                ? modelData.cells.split(",").map(function (c) {
                      var p = c.split(":")
                      return { id: parseInt(p[0]), video: p[1] === "1", favorite: p[2] === "1",
                               x: parseInt(p[3] || 0), y: parseInt(p[4] || 0),
                               w: parseInt(p[5] || 1), h: parseInt(p[6] || 1) }
                  })
                : []
            // big videos here, for pickClip()
            readonly property var clips: cells.filter(function (c) { return c.video && c.w > 1 })

            Label {
                id: dayLabel
                visible: modelData.t === "d"
                anchors { left: parent.left; leftMargin: Theme.horizontalPageMargin
                          right: parent.right; rightMargin: Theme.horizontalPageMargin
                          bottom: parent.bottom; bottomMargin: Theme.paddingSmall }
                truncationMode: TruncationMode.Fade
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.highlightColor
                text: modelData.t === "d" ? page.dayText(modelData) : ""
            }
            MouseArea {
                anchors.fill: parent
                enabled: modelData.t === "d" && page.selecting
                onClicked: page.toggleDay(index)
            }

            Repeater {
                model: band.cells
                delegate: CloudThumb {
                        readonly property real step: page.cell + page.gap
                        app: page.app
                        x: modelData.x * step
                        y: modelData.y * step
                        width: modelData.w * step - page.gap
                        height: modelData.h * step - page.gap
                        fileId: modelData.id
                        video: modelData.video
                        favorite: modelData.favorite
                        big: modelData.w > 1
                        play: big && video && page.clipId === fileId && !page.selecting
                              && page.status === PageStatus.Active
                              && Qt.application.state === Qt.ApplicationActive
                        neighbours: page.allIds
                        selecting: page.selecting
                        selected: page.selectionRevision >= 0 && page.selected[fileId] === true
                        onToggled: page.toggle(fileId)
                        onHeld: page.selecting ? page.toggle(fileId) : page.startSelecting(fileId)
                }
            }
        }

        BusyIndicator {
            anchors.centerIn: parent
            size: BusyIndicatorSize.Large
            running: view.count === 0 && (app.cloud.syncing || !page.loaded
                                          || page.working === "trashsync")
        }
        ViewPlaceholder {
            enabled: view.count === 0 && page.loaded && page.working !== "trashsync"
            text: page.trash ? qsTr("The trash is empty")
                  : page.mode === "memories" ? qsTr("No memories for today yet")
                  : page.dups ? qsTr("No duplicates")
                  : app.cloud.syncing ? qsTr("Reading the list of your photos")
                  : qsTr("No photos")
            hintText: page.mode !== "" ? ""
                      : app.cloud.syncing
                        ? qsTr("The first time takes a few minutes for many photos; later only "
                               + "changes are read")
                        : (page.albumId === 0 ? qsTr("Pull down to update") : "")
        }
        VerticalScrollDecorator { }
    }

    // While selecting: what to do with the selection
    Rectangle {
        id: selectionBar
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: Theme.itemSizeLarge + Theme.paddingMedium
        visible: page.selecting
        color: Theme.rgba(Theme.highlightDimmerColor, 0.92)

        Row {
          anchors.centerIn: parent
          // the actions wait for a selection; Cancel (outside this group) always works
          Row {
            enabled: page.selectedCount > 0 && page.working === ""
            opacity: enabled ? 1 : 0.4

            ActionButton {      // favorites
                visible: page.mine
                icon: page.albumType === "favorites" ? "image://theme/icon-m-favorite-selected"
                                                     : "image://theme/icon-m-favorite"
                label: page.albumType === "favorites" ? qsTr("Unfavorite") : qsTr("Favorite")
                onClicked: page.act("favorite", "cloud_favorite",
                                    [page.selectedIds(), page.albumType !== "favorites"])
            }
            ActionButton {
                visible: page.mine
                icon: "image://theme/icon-m-add"
                label: qsTr("To album")
                onClicked: page.toAlbum(false)
            }
            ActionButton {
                visible: page.mine && page.albumId > 0
                icon: "image://theme/icon-m-folder"
                label: qsTr("Move")
                onClicked: page.toAlbum(true)
            }
            ActionButton {
                visible: !page.trash
                icon: "image://theme/icon-m-device-download"
                label: qsTr("Save")
                onClicked: page.act("save", "cloud_save_many", [page.selectedIds()])
            }
            ActionButton {
                visible: page.mine
                icon: "image://theme/icon-m-delete"
                label: qsTr("Trash")
                onClicked: page.trashSelected()
            }
            ActionButton {      // duplicates: the copies that stay join the albums first
                visible: page.dups
                icon: "image://theme/icon-m-delete"
                label: qsTr("Remove")
                onClicked: {
                    var ids = page.selectedIds()
                    remorse.execute(qsTr("Moving %n extra cop(ies) to Ente's trash", "", ids.length),
                                    function () { page.act("dedupe", "cloud_trash_duplicates", [ids]) })
                }
            }
            ActionButton {
                visible: page.trash
                icon: "image://theme/icon-m-backup"
                label: qsTr("Restore")
                onClicked: page.act("restore", "cloud_restore", [page.selectedIds()])
            }
            ActionButton {
                visible: page.trash
                icon: "image://theme/icon-m-delete"
                label: qsTr("Delete")
                onClicked: {
                    var ids = page.selectedIds()
                    remorse.execute(qsTr("Deleting %n photo(s) for good", "", ids.length),
                                    function () { page.act("delete", "cloud_delete_forever", [ids]) })
                }
            }
          }
          ActionButton {
              enabled: page.working === ""
              icon: "image://theme/icon-m-clear"
              label: qsTr("Cancel")
              onClicked: page.stopSelecting()
          }
        }
        BusyIndicator {
            anchors { right: parent.right; rightMargin: Theme.paddingMedium; top: parent.top
                      topMargin: Theme.paddingSmall }
            size: BusyIndicatorSize.Small
            running: page.working !== "" && page.working !== "trashsync"
        }
    }
}
