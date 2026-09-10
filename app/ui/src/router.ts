import { createRouter, createWebHashHistory } from "vue-router";

// Hash history: the Ingress prefix is unknown at build time, and history mode
// would 404 on reload behind it.
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/library" },
    { path: "/library", name: "library", component: () => import("@/views/LibraryView.vue") },
    { path: "/order", name: "order", component: () => import("@/views/OrderView.vue") },
    { path: "/import", name: "import", component: () => import("@/views/ImportView.vue") },
    { path: "/system", name: "system", component: () => import("@/views/SystemView.vue") },
    { path: "/:pathMatch(.*)*", redirect: "/library" },
  ],
});
