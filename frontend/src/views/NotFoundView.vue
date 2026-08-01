<template>
  <div class="page not-found">
    <h1 class="nf-code">
      404
    </h1>
    <p class="nf-msg">
      页面不存在或已被移除
    </p>
    <router-link
      class="secondary nf-link"
      to="/dashboard"
    >
      返回仪表盘
    </router-link>
  </div>
</template>

<style scoped>
.not-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 50vh;
  gap: 8px;
}
.nf-code {
  font-size: 64px;
  margin: 0;
  color: var(--color-text-muted);
}
.nf-msg {
  color: var(--color-text-muted);
}
.nf-link {
  margin-top: 12px;
}
</style>
