import { createRouter, createWebHistory } from 'vue-router';
import ChatView from '../views/ChatView.vue';
import ImportView from '../views/ImportView.vue';
import LoginView from '../views/LoginView.vue';
import { isAuthenticated } from '../services/auth';

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
    { path: '/chat', name: 'chat', component: ChatView, meta: { requiresAuth: true } },
    { path: '/import', name: 'import', component: ImportView, meta: { requiresAuth: true } },
    { path: '/:pathMatch(.*)*', redirect: '/chat' }
  ]
});

router.beforeEach((to) => {
  const authed = isAuthenticated();

  if (to.meta.public && authed) {
    const redirect = to.query.redirect;
    return typeof redirect === 'string' && redirect.startsWith('/') ? redirect : '/chat';
  }

  if (to.meta.requiresAuth && !authed) {
    return {
      name: 'login',
      query: { redirect: to.fullPath }
    };
  }

  return true;
});
