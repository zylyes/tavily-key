<script setup lang="ts">
import { useToast } from '@/composables/useToast'
import { WEBVIEW_ONLY_MSG, inWebview, winResize } from '@/utils/webview'

/* ResizeHandles —— 无边框窗口 8 方向边缘缩放热区（仅 body.webview 时显示）
   data-dir 取值与后端 _HT_HITS 键一一对应，不可改。 */

const toast = useToast()

const DIRS = [
  { cls: 'r-n', dir: 'top', cursor: 'n-resize' },
  { cls: 'r-s', dir: 'bottom', cursor: 's-resize' },
  { cls: 'r-e', dir: 'right', cursor: 'e-resize' },
  { cls: 'r-w', dir: 'left', cursor: 'w-resize' },
  { cls: 'r-nw', dir: 'top-left', cursor: 'nw-resize' },
  { cls: 'r-ne', dir: 'top-right', cursor: 'ne-resize' },
  { cls: 'r-sw', dir: 'bottom-left', cursor: 'sw-resize' },
  { cls: 'r-se', dir: 'bottom-right', cursor: 'se-resize' },
]

function onStart(e: MouseEvent, dir: string): void {
  if (!inWebview()) {
    toast.info(WEBVIEW_ONLY_MSG)
    return
  }
  e.preventDefault()
  winResize(dir)
}
</script>

<template>
  <div
    v-for="d in DIRS"
    :key="d.dir"
    class="resize-handle"
    :class="d.cls"
    :data-dir="d.dir"
    :style="{ cursor: d.cursor }"
    @mousedown="onStart($event, d.dir)"
  />
</template>

<style scoped>
.resize-handle {
  position: fixed;
  z-index: var(--z-resize);
  display: none;
}
body.webview .resize-handle { display: block; }

.r-n { top: 0; left: 10px; right: 10px; height: 5px; }
.r-s { bottom: 0; left: 10px; right: 10px; height: 5px; }
.r-e { right: 0; top: 10px; bottom: 10px; width: 5px; }
.r-w { left: 0; top: 10px; bottom: 10px; width: 5px; }
.r-nw { top: 0; left: 0; width: 12px; height: 12px; }
.r-ne { top: 0; right: 0; width: 12px; height: 12px; }
.r-sw { bottom: 0; left: 0; width: 12px; height: 12px; }
.r-se { bottom: 0; right: 0; width: 12px; height: 12px; }
</style>
