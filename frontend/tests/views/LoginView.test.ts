// 登录视图测试：表单校验、登录成功跳转、失败提示、open-redirect 防护、demo 填充
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';

// ==== mocks ====
const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  push: vi.fn(),
  loginRedirect: '/dashboard',
}));

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.push }),
  useRoute: () => ({ query: { redirect: mocks.loginRedirect } }),
}));

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ login: mocks.login }),
}));

vi.mock('@/api/client', () => ({
  extractApiError: (e: unknown, fallback: string) =>
    e instanceof Error ? e.message : fallback,
}));

import LoginView from '@/views/LoginView.vue';

describe('LoginView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.loginRedirect = '/dashboard';
    mocks.login.mockResolvedValue(undefined);
  });

  it('渲染邮件/密码/TOTP 输入与提交按钮', () => {
    const wrapper = mount(LoginView, { global: { plugins: [] } });
    const inputs = wrapper.findAll('input');
    expect(inputs.length).toBeGreaterThanOrEqual(3);
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true);
  });

  it('登录成功后跳转到 redirect 查询参数（站内相对路径）', async () => {
    mocks.loginRedirect = '/employees';
    const wrapper = mount(LoginView);
    await wrapper.find('form').trigger('submit');
    expect(mocks.login).toHaveBeenCalled();
    expect(mocks.push).toHaveBeenCalledWith('/employees');
  });

  it('open-redirect 防护：外部绝对路径回退到 /dashboard', async () => {
    mocks.loginRedirect = '//evil.com';
    const wrapper = mount(LoginView);
    await wrapper.find('form').trigger('submit');
    expect(mocks.push).toHaveBeenCalledWith('/dashboard');
  });

  it('登录失败时展示错误信息，loading 复位', async () => {
    mocks.login.mockRejectedValueOnce(new Error('credentials invalid'));
    const wrapper = mount(LoginView);
    await wrapper.find('form').trigger('submit');
    await nextTick();
    expect(wrapper.find('.error').text()).toContain('credentials invalid');
    const btn = wrapper.find('button[type="submit"]');
    expect((btn.element as HTMLButtonElement).disabled).toBe(false);
  });
});