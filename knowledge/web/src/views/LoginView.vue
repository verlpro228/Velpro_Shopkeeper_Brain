<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { LockKeyhole, ShieldCheck, Sparkles, UserRound } from '@lucide/vue';
import { isAuthenticated, login } from '../services/auth';

const router = useRouter();
const route = useRoute();
const submitting = ref(false);
const errorMessage = ref('');

const form = reactive({
  username: '',
  password: '',
  agreed: false
});

const redirectTarget = computed(() => {
  const redirect = route.query.redirect;
  return typeof redirect === 'string' && redirect.startsWith('/') ? redirect : '/chat';
});

if (isAuthenticated()) {
  router.replace(redirectTarget.value);
}

async function handleLogin(): Promise<void> {
  if (submitting.value) return;
  submitting.value = true;
  errorMessage.value = '';

  try {
    const result = await login({
      username: form.username,
      password: form.password,
      agreed: form.agreed
    });

    if (!result.ok) {
      errorMessage.value = result.message || '登录失败';
      return;
    }

    await router.replace(redirectTarget.value);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-card glass-panel">
      <div class="login-brand">
        <div class="brand-mark login-mark">
          <Sparkles :size="24" />
        </div>
        <div>
          <p class="eyebrow">Velpro Brain</p>
          <h1>知识库工作台登录</h1>
        </div>
      </div>

      <p class="login-intro">请输入账号密码，并确认使用协议和须知后进入系统。</p>

      <form class="login-form" @submit.prevent="handleLogin">
        <label class="login-field">
          <span>账号</span>
          <div class="login-input">
            <UserRound :size="18" />
            <input
              v-model="form.username"
              autocomplete="username"
              placeholder="请输入账号"
              type="text"
            />
          </div>
        </label>

        <label class="login-field">
          <span>密码</span>
          <div class="login-input">
            <LockKeyhole :size="18" />
            <input
              v-model="form.password"
              autocomplete="current-password"
              placeholder="请输入密码"
              type="password"
            />
          </div>
        </label>

        <label class="agreement-row">
          <input v-model="form.agreed" type="checkbox" />
          <span>我已阅读并同意使用协议和须知</span>
        </label>

        <p v-if="errorMessage" class="login-error">{{ errorMessage }}</p>

        <button class="primary-button login-button" type="submit" :disabled="submitting">
          <ShieldCheck :size="18" />
          {{ submitting ? '登录中...' : '登录' }}
        </button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 22px;
  background:
    radial-gradient(circle at 18% 20%, rgba(37, 99, 235, 0.12), transparent 34%),
    radial-gradient(circle at 82% 18%, rgba(14, 116, 144, 0.1), transparent 30%),
    #fff;
}

.login-card {
  width: min(460px, 100%);
  padding: 28px;
  border-radius: 32px;
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 14px;
}

.login-mark {
  width: 52px;
  height: 52px;
}

.login-brand h1 {
  margin: 0;
  color: #0f172a;
  font-size: 24px;
}

.login-intro {
  margin: 18px 0 22px;
  color: var(--muted);
}

.login-form {
  display: grid;
  gap: 16px;
}

.login-field {
  display: grid;
  gap: 8px;
  color: #26364d;
  font-weight: 800;
}

.login-input {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  min-height: 50px;
  padding: 0 14px;
  border-radius: 18px;
  color: #64748b;
  background: rgba(255, 255, 255, 0.74);
  border: 1px solid rgba(255, 255, 255, 0.9);
  box-shadow: inset 0 1px 4px rgba(54, 68, 88, 0.08);
}

.login-input input {
  width: 100%;
  border: 0;
  outline: none;
  color: #152033;
  background: transparent;
}

.agreement-row {
  display: flex;
  align-items: center;
  gap: 9px;
  color: #475569;
  cursor: pointer;
  user-select: none;
  font-size: 14px;
}

.agreement-row input {
  width: 16px;
  height: 16px;
  accent-color: #111827;
}

.login-error {
  margin: 0;
  color: var(--red);
  font-size: 14px;
  font-weight: 700;
}

.login-button {
  width: 100%;
  min-height: 48px;
}
</style>
