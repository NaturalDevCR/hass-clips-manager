<script setup lang="ts">
defineProps<{ label: string; help?: string }>();
</script>

<template>
  <label class="flex flex-col gap-1">
    <span class="text-xs font-medium text-ink">{{ label }}</span>
    <slot />
    <span v-if="help" class="text-xs leading-snug text-muted">{{ help }}</span>
  </label>
</template>
