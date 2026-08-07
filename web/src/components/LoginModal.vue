<script setup lang="ts">
import { ref, watch } from 'vue'
import { useAuth } from '@/composables/useAuth'
import GButton from './GButton.vue'
import GInput from './GInput.vue'
import GModal from './GModal.vue'

/* LoginModal —— 401 鉴权门（行为对齐旧版：弹窗不可跳过，验证后自动重试挂起请求） */

const { state, login } = useAuth()
const token = ref('')

async function submit(): Promise<void> {
  const ok = await login(token.value)
  if (ok) token.value = ''
}

// 每次弹窗打开时清空输入
watch(() => state.required, (v) => {
  if (v) token.value = ''
})
</script>

<template>
  <GModal
    :open="state.required"
    title=""
    width="380px"
    :closable="false"
    class="login-modal"
  >
    <div class="login-box">
      <img class="login-logo" :src="'/logo.png'" alt="Tavily" draggable="false" />
      <h3 class="login-title">需要访问令牌</h3>
      <p class="login-desc">该实例已启用访问鉴权，请输入 auth_token 继续。</p>
      <form @submit.prevent="submit">
        <GInput
          v-model="token"
          type="password"
          placeholder="访问令牌"
          mono
          name="tavily-auth-token"
          autocomplete="off"
          @enter="submit"
        />
        <div class="login-err" role="alert">{{ state.error }}</div>
        <GButton
          variant="primary"
          type="submit"
          :busy="state.busy"
          :disabled="!token.trim()"
          style="width: 100%"
        >
          验证并继续
        </GButton>
      </form>
    </div>
  </GModal>
</template>

<style scoped>
.login-box { text-align: center; padding: 6px 4px 2px; }
.login-logo {
  width: 52px;
  height: 52px;
  margin: 0 auto 14px;
  filter: drop-shadow(0 8px 20px rgba(109, 124, 255, .4));
}
.login-title { font-size: 15px; margin-bottom: 6px; }
.login-desc { font-size: 11.5px; color: var(--text-2); margin-bottom: 18px; }
.login-err {
  min-height: 18px;
  margin: 8px 0 10px;
  font-size: 11.5px;
  color: var(--danger);
  text-align: left;
}
</style>
