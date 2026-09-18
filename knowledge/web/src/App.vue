<script setup lang="ts">
import { computed, type Component } from 'vue';
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router';
import {
  BotMessageSquare,
  ChevronRight,
  CloudUpload,
  LogOut,
  MessageSquareText,
  Sparkles
} from '@lucide/vue';
import { getAuthSession, logout } from './services/auth';

interface NavItem {
  to: string;
  label: string;
  subtitle: string;
  icon: Component;
}

const route = useRoute();
const router = useRouter();
const navItems: NavItem[] = [
  {
    to: '/chat',
    label: '知识对话',
    subtitle: 'SSE 流式问答',
    icon: MessageSquareText
  },
  {
    to: '/import',
    label: '文件导入',
    subtitle: 'PDF / Markdown',
    icon: CloudUpload
  }
];

const current = computed(() => navItems.find((item) => item.to === route.path) || navItems[0]);
const isLoginRoute = computed(() => route.name === 'login');
const authSession = computed(() => {
  route.fullPath;
  return getAuthSession();
});

function handleLogout(): void {
  logout();
  router.replace('/login');
}
</script>

<template>
  <RouterView v-if="isLoginRoute" />
  <div v-else class="app-shell">
    <aside class="sidebar glass-panel">
      <div class="brand-block">
        <div class="brand-mark">
          <Sparkles :size="22" />
        </div>
        <div>
          <p class="eyebrow">Velpro Brain</p>
          <h1>知识库工作台</h1>
        </div>
      </div>

      <nav class="nav-stack" aria-label="主导航">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          v-slot="{ href, navigate, isActive }"
          custom
          :to="item.to"
        >
          <a :href="href" class="nav-item" :class="{ active: isActive }" @click="navigate">
            <span class="nav-icon">
              <component :is="item.icon" :size="19" />
            </span>
            <span class="nav-copy">
              <strong>{{ item.label }}</strong>
              <small>{{ item.subtitle }}</small>
            </span>
            <ChevronRight class="nav-arrow" :size="17" />
          </a>
        </RouterLink>
      </nav>

      <div class="sidebar-footer">
        <div class="user-card">
          <div>
            <strong>{{ authSession?.username || '未登录' }}</strong>
            <span>本地登录态已启用</span>
          </div>
          <button class="icon-button" type="button" title="退出登录" @click="handleLogout">
            <LogOut :size="17" />
          </button>
        </div>
      </div>
    </aside>

    <main class="workspace">
      <section class="route-surface glass-panel">
        <div class="status-chip floating">
          <BotMessageSquare :size="17" />
          <span>轻量级知识库</span>
        </div>
        <RouterView v-slot="{ Component }">
          <KeepAlive>
            <component :is="Component" />
          </KeepAlive>
        </RouterView>
      </section>
    </main>
  </div>
</template>
