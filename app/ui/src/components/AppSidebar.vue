<script setup lang="ts">
const sections = [
  { name: "library", label: "Library", glyph: "▤" },
  { name: "order", label: "Playback order", glyph: "↕" },
  { name: "import", label: "Import", glyph: "↑" },
  { name: "system", label: "System", glyph: "⚙" },
];
</script>

<template>
  <nav
    aria-label="Library Manager sections"
    class="flex shrink-0 gap-1 border-line bg-surface p-2 max-md:order-last max-md:justify-around max-md:border-t md:w-56 md:flex-col md:border-r md:p-3"
  >
    <p class="mb-3 px-3 text-xs font-semibold tracking-widest text-muted uppercase max-md:hidden">
      Cinema
    </p>
    <RouterLink
      v-for="section in sections"
      :key="section.name"
      :to="{ name: section.name }"
      class="flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-muted transition hover:bg-hover hover:text-ink max-md:flex-col max-md:gap-1 max-md:text-xs"
      active-class="bg-raised text-accent!"
    >
      <span aria-hidden="true" class="text-base">{{ section.glyph }}</span>
      <span>{{ section.label }}</span>
    </RouterLink>
  </nav>
</template>
