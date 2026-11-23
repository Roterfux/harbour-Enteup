// Enteup - SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick 2.6
import Sailfish.Silica 1.0

// A photo full screen, with its name and capture time on top; tap to hide them.
// Pinch or double-tap to zoom.
// A photo on the phone: path. A photo in the account: app + cloudId (+ fallback, its
// thumbnail, shown while the photo loads and instead of photos this phone cannot show);
// with ids (the grid's photos, in order) and index, swipe sideways (or use the arrows) to
// the next or previous one.
Page {
    id: page
    property var app: null
    property string path
    property string name
    property real taken: 0
    property real cloudId: 0            // not int: ids may outgrow 32 bits
    property var ids: []
    property int index: -1
    property string fallback: ""
    property string note: ""
    property string problem: ""
    property real progress: -1          // 0..1 while a photo in the account loads
    property bool chrome: true
    property string caption: ""         // written in Ente
    property string videoPath: ""       // a video, or the moving part of a live photo
    property bool motion: false         // videoPath is a live photo's
    property bool playing: false        // the video is shown (over the picture)
    property bool own: false            // in one of the user's albums: may go to the trash
    property bool favorite: false
    property bool inTrash: false        // a photo in Ente's trash: restore or delete it
    property string working: ""         // save | share | trash | favorite | restore | delete
    property string actionNote: ""      // what the last action did

    readonly property bool canSwipe: ids.length > 1 && flick.zoom <= 1.01

    function save(share) {
        working = share ? "share" : "save"
        actionNote = ""
        app.call("cloud_save", [cloudId, share])
    }

    allowedOrientations: Orientation.All

    readonly property bool usingFallback: path === "" || photo.status === Image.Error

    function fileUrl(path) {
        return "file://" + encodeURI(path).replace(/#/g, "%23").replace(/\?/g, "%3F")
    }

    // Load what belongs to cloudId
    function load() {
        if (!(cloudId > 0 && app)) return
        var id = cloudId
        app.call("cloud_details", [id], function (info) {
            if (id !== page.cloudId || info.title === undefined) return
            page.name = info.title
            page.taken = info.taken
            page.caption = info.caption || ""
            page.own = info.own === true
            page.favorite = info.favorite === true
            page.inTrash = info.trash === true
        })
        progress = 0
        app.call("cloud_open", [id])
    }

    // Show the photo at position i of ids
    function show(i) {
        if (i < 0 || i >= ids.length) return
        index = i
        cloudId = ids[i]
        var thumb = app.thumbCache[cloudId]
        fallback = thumb ? thumb : ""
        path = ""
        name = ""
        taken = 0
        note = ""
        problem = ""
        caption = ""
        videoPath = ""
        motion = false
        playing = false
        own = false
        favorite = false
        actionNote = ""
        flick.reset()
        load()
    }

    // Swipe: slide out, show the neighbour, slide it in
    function go(step) {
        var target = index + step
        if (target < 0 || target >= ids.length || slideOut.running || slideIn.running) {
            stage.x = 0
            return
        }
        slideOut.to = step > 0 ? -page.width : page.width
        slideOut.step = step
        slideOut.start()
    }

    // The photo left the account (trash) or the trash (restored, deleted): on to the next
    function dropCurrent() {
        var rest = ids.slice(0)
        rest.splice(index, 1)
        ids = rest
        if (rest.length === 0) pageStack.pop()
        else show(Math.min(index, rest.length - 1))
    }

    NumberAnimation {
        id: slideOut
        property int step: 0
        target: stage
        property: "x"
        duration: 150
        easing.type: Easing.InQuad
        onStopped: {
            page.show(page.index + step)
            stage.x = -to
            slideIn.start()
        }
    }
    NumberAnimation {
        id: slideIn
        target: stage
        property: "x"
        to: 0
        duration: 170
        easing.type: Easing.OutQuad
    }
    NumberAnimation {
        id: slideBack
        target: stage
        property: "x"
        to: 0
        duration: 150
    }

    Component.onCompleted: {
        if (cloudId > 0 && ids.length > 0 && index < 0) index = ids.indexOf(cloudId)
        load()
    }

    Connections {
        target: page.app
        ignoreUnknownSignals: true
        onFullReady: if (fullId === page.cloudId) {
            if (result.progress !== undefined) {
                page.progress = result.progress
            } else {
                page.progress = -1
                if (result.error) page.problem = result.error
                else {
                    page.note = result.note || ""
                    page.motion = result.motion === true
                    page.videoPath = result.video || ""
                    page.path = result.path || ""
                    // a video plays at once; a live photo moves on a tap of its button
                    page.playing = page.videoPath !== "" && !page.motion
                }
            }
        }
        onSavedReady: if (savedId === page.cloudId) {
            var sharing = page.working === "share"
            page.working = ""
            if (result.error) {
                page.actionNote = result.error
            } else if (sharing) {
                if (!page.app.shareFile(result.paths[0]))
                    page.actionNote = qsTr("No share menu on this phone; saved to %1").arg(result.folder)
            } else {
                page.actionNote = qsTr("Saved to %1").arg(result.folder)
            }
        }
        onTrashedReady: if (trashedId === page.cloudId) {
            page.working = ""
            if (result.error) page.actionNote = result.error
            else page.dropCurrent()
        }
        onActedReady: {
            var what = page.working
            if (what !== "favorite" && what !== "restore" && what !== "delete") return
            page.working = ""
            if (result.error) {
                page.actionNote = result.error
            } else if (what === "favorite") {
                page.favorite = result.what === "favorite"
            } else if (result.done > 0) {
                page.dropCurrent()
            }
        }
    }

    RemorsePopup { id: remorse }

    Rectangle {
        anchors.fill: parent
        color: "black"
    }

    // Everything that slides when swiping
    Item {
        id: stage
        width: page.width
        height: page.height

        // Zooming resizes the flickable's content around a point (the middle of the pinch,
        // or the double tap), so that point stays where the fingers are. Scaling the
        // picture instead would leave the view at its top left corner.
        Flickable {
            id: flick
            objectName: "flick"
            anchors.fill: parent
            contentWidth: width
            contentHeight: height
            boundsBehavior: Flickable.StopAtBounds
            interactive: zoom > 1.01

            property real zoom: 1
            readonly property real maxZoom: 6

            // point: in the content's coordinates
            function zoomTo(factor, point) {
                factor = Math.max(1, Math.min(maxZoom, factor))
                if (factor <= 1.001) {      // all the way out: the whole picture, at once
                    reset()
                    return
                }
                resizeContent(width * factor, height * factor, point)
                zoom = factor
            }
            function reset() {
                zoom = 1
                contentWidth = width
                contentHeight = height
                contentX = 0
                contentY = 0
            }
            onWidthChanged: reset()         // the phone was turned
            onHeightChanged: reset()

            PinchArea {
                width: flick.contentWidth
                height: flick.contentHeight
                property real startZoom: 1
                onPinchStarted: startZoom = flick.zoom
                onPinchUpdated: flick.zoomTo(startZoom * pinch.scale, pinch.center)
                onPinchFinished: flick.returnToBounds()

                Image {
                    id: small
                    anchors.fill: photo
                    visible: page.usingFallback || photo.status !== Image.Ready
                    source: page.fallback !== "" ? page.fileUrl(page.fallback) : ""
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    smooth: true
                }

                Image {
                    id: photo
                    width: flick.contentWidth
                    height: flick.contentHeight
                    source: page.path !== "" ? page.fileUrl(page.path) : ""
                    // the screen's size twice over: sharp when zoomed, without a 12 MP texture
                    sourceSize.width: Math.max(Screen.width, Screen.height) * 2
                    sourceSize.height: Math.max(Screen.width, Screen.height) * 2
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    autoTransform: true
                    smooth: true
                }

                MouseArea {
                    id: touch
                    anchors.fill: parent
                    property real startX: 0
                    property real startY: 0
                    property bool sideways: false       // this touch is a swipe
                    preventStealing: sideways

                    onPressed: {
                        var p = mapToItem(page, mouse.x, mouse.y)
                        startX = p.x
                        startY = p.y
                        sideways = false
                    }
                    onPositionChanged: {
                        if (!page.canSwipe) return
                        var p = mapToItem(page, mouse.x, mouse.y)
                        var dx = p.x - startX
                        if (!sideways && Math.abs(dx) > Theme.startDragDistance
                                && Math.abs(dx) > Math.abs(p.y - startY))
                            sideways = true
                        if (sideways) stage.x = dx
                    }
                    onReleased: {
                        if (!sideways) return
                        var dx = stage.x
                        if (Math.abs(dx) > page.width * 0.2) page.go(dx < 0 ? 1 : -1)
                        else slideBack.start()
                    }
                    onCanceled: if (sideways) slideBack.start()
                    onClicked: if (!sideways) page.chrome = !page.chrome
                    // zoom in on the spot that was tapped; again: back to the whole picture
                    onDoubleClicked: {
                        if (flick.zoom > 1.01) flick.reset()
                        else flick.zoomTo(2.5, Qt.point(mouse.x, mouse.y))
                        flick.returnToBounds()
                    }
                }
            }
        }
    }

    Column {
        anchors.centerIn: parent
        width: parent.width - 2 * Theme.horizontalPageMargin
        spacing: Theme.paddingMedium
        visible: photo.status === Image.Loading || page.progress >= 0
        BusyIndicator {
            anchors.horizontalCenter: parent.horizontalCenter
            size: BusyIndicatorSize.Large
            running: parent.visible
        }
        Label {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            visible: page.progress > 0
            color: Theme.highlightColor
            text: qsTr("Loading %1%").arg(Math.round(page.progress * 100))
        }
    }

    Label {
        anchors.centerIn: parent
        width: parent.width - 2 * Theme.horizontalPageMargin
        visible: text !== ""
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        color: Theme.highlightColor
        text: page.problem !== "" ? page.problem
              : page.cloudId === 0 && photo.status === Image.Error
                ? qsTr("This photo is no longer on the phone") : ""
    }

    // Videos, and the moving part of live photos, over the picture. A Loader: without the
    // phone's multimedia module only this part fails, not the page.
    Loader {
        id: videoLoader
        anchors.fill: parent
        active: page.playing && page.videoPath !== ""
        source: "VideoPlayer.qml"
        onStatusChanged: if (status === Loader.Error) {
            page.playing = false
            page.problem = qsTr("Videos cannot be played on this phone")
        }
    }
    Binding {
        target: videoLoader.item
        property: "video"
        value: page.fileUrl(page.videoPath)
        when: videoLoader.status === Loader.Ready
    }
    // a live photo's movement ends: back to the picture
    Connections {
        target: page.motion ? videoLoader.item : null
        ignoreUnknownSignals: true
        onEndedChanged: if (videoLoader.item.ended) page.playing = false
    }

    // Live photo: play its movement
    Rectangle {
        anchors { right: parent.right; bottom: parent.bottom; margins: Theme.paddingLarge
                  bottomMargin: (panel.visible ? panel.height : 0) + Theme.paddingLarge }
        visible: page.motion && !page.playing
        width: Theme.itemSizeMedium
        height: width
        radius: width / 2
        color: Theme.rgba("black", 0.6)
        Label {
            anchors.centerIn: parent
            font.pixelSize: Theme.fontSizeExtraSmall
            font.bold: true
            color: "white"
            text: "LIVE"
        }
        MouseArea {
            anchors.fill: parent
            onClicked: page.playing = true
        }
    }

    // Previous / next, also while a video plays (its taps belong to the player)
    IconButton {
        anchors { left: parent.left; verticalCenter: parent.verticalCenter }
        visible: page.ids.length > 1 && page.index > 0 && (page.chrome || page.playing)
        icon.source: "image://theme/icon-m-left"
        onClicked: page.go(-1)
    }
    IconButton {
        anchors { right: parent.right; verticalCenter: parent.verticalCenter }
        visible: page.ids.length > 1 && page.index < page.ids.length - 1
                 && (page.chrome || page.playing)
        icon.source: "image://theme/icon-m-right"
        onClicked: page.go(1)
    }

    Rectangle {
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: header.height + Theme.paddingLarge
        opacity: page.chrome ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { FadeAnimator { } }
        gradient: Gradient {
            GradientStop { position: 0; color: Qt.rgba(0, 0, 0, 0.75) }
            GradientStop { position: 1; color: "transparent" }
        }
        PageHeader {
            id: header
            title: page.taken > 0 ? Qt.formatDateTime(new Date(page.taken * 1000),
                                                      "d MMM yyyy, hh:mm:ss")
                                  : (page.name !== "" ? qsTr("Time unknown") : "")
            description: page.inTrash ? qsTr("In Ente's trash") + " · " + page.name
                         : page.ids.length > 1 ? (page.index + 1) + "/" + page.ids.length
                                                  + " · " + page.name
                         : page.name
        }
    }

    // Photos in the account: save, share, favorite, move to Ente's trash; for photos in
    // the trash: restore, delete for good. Shown with the header, and always while a video
    // plays (its taps belong to the player).
    Row {
        id: actions
        anchors { right: parent.right; rightMargin: Theme.paddingMedium
                  top: parent.top; topMargin: header.height }
        visible: page.cloudId > 0 && page.name !== "" && (page.chrome || page.playing)
        enabled: page.working === ""
        opacity: enabled ? 1 : 0.4
        IconButton {
            visible: page.own && !page.inTrash
            icon.source: page.favorite ? "image://theme/icon-m-favorite-selected"
                                       : "image://theme/icon-m-favorite"
            onClicked: {
                page.working = "favorite"
                page.actionNote = ""
                page.app.call("cloud_favorite", [[page.cloudId], !page.favorite])
            }
        }
        IconButton {
            icon.source: "image://theme/icon-m-device-download"   // the phone has no icon-m-download
            onClicked: page.save(false)
        }
        IconButton {
            icon.source: "image://theme/icon-m-share"
            onClicked: page.save(true)
        }
        IconButton {
            visible: page.own && !page.inTrash
            icon.source: "image://theme/icon-m-delete"
            onClicked: remorse.execute(qsTr("Moving to Ente's trash"), function () {
                page.working = "trash"
                page.actionNote = ""
                page.app.call("cloud_trash", [page.cloudId])
            })
        }
        IconButton {
            visible: page.inTrash
            icon.source: "image://theme/icon-m-backup"
            onClicked: {
                page.working = "restore"
                page.actionNote = ""
                page.app.call("cloud_restore", [[page.cloudId]])
            }
        }
        IconButton {
            visible: page.inTrash
            icon.source: "image://theme/icon-m-delete"
            onClicked: remorse.execute(qsTr("Deleting for good"), function () {
                page.working = "delete"
                page.actionNote = ""
                page.app.call("cloud_delete_forever", [[page.cloudId]])
            })
        }
    }
    BusyIndicator {
        anchors { right: actions.left; verticalCenter: actions.verticalCenter }
        size: BusyIndicatorSize.Small
        running: page.working !== ""
    }
    Rectangle {
        anchors { left: parent.left; right: parent.right; top: actions.bottom
                  margins: Theme.horizontalPageMargin }
        height: actionLabel.height + 2 * Theme.paddingMedium
        visible: page.actionNote !== ""
        radius: Theme.paddingSmall
        color: Qt.rgba(0, 0, 0, 0.7)
        Label {
            id: actionLabel
            anchors.centerIn: parent
            width: parent.width - 2 * Theme.paddingMedium
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
            font.pixelSize: Theme.fontSizeSmall
            color: Theme.highlightColor
            text: page.actionNote
        }
        MouseArea {
            anchors.fill: parent
            onClicked: page.actionNote = ""
        }
    }

    // The caption written in Ente, and why a preview stands in for the picture
    Rectangle {
        id: panel
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: texts.height + 2 * Theme.paddingLarge
        visible: (captionLabel.text !== "" || noteLabel.text !== "") && page.chrome
                 && !page.playing
        color: Qt.rgba(0, 0, 0, 0.6)
        Column {
            id: texts
            anchors.centerIn: parent
            width: parent.width - 2 * Theme.horizontalPageMargin
            spacing: Theme.paddingSmall
            Label {
                id: captionLabel
                width: parent.width
                visible: text !== ""
                wrapMode: Text.Wrap
                color: "white"
                text: page.caption
            }
            Label {
                id: noteLabel
                width: parent.width
                visible: text !== ""
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.highlightColor
                text: page.note !== "" ? page.note
                      : page.cloudId > 0 && page.path !== "" && photo.status === Image.Error
                        ? qsTr("This phone cannot show this picture; this is Ente's preview.")
                        : ""
            }
        }
    }
}
