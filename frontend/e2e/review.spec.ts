import { test, expect } from '@playwright/test';

test('register, review, refresh persisted history, logout, and login', async ({
  page,
}, testInfo) => {
  const name = `reviewer-${Date.now()}`;
  await page.goto('/');
  await page.screenshot({
    path: testInfo.outputPath('sign-in.png'),
    fullPage: true,
  });
  await page.getByRole('button', { name: 'Create an account' }).click();
  await page.getByLabel('Username').fill(name);
  await page
    .getByLabel('Password', { exact: true })
    .fill('correct-horse-battery');
  await page
    .getByRole('button', { name: 'Create account', exact: true })
    .click();
  await expect(
    page.getByText('Simulated model · no Qwen inference', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('status', { name: 'Service status' }),
  ).toContainText('Accepting submissions');
  await page.getByRole('button', { name: /Try a small example/ }).click();
  await page.getByRole('button', { name: 'Run review' }).click();
  await expect(page.getByRole('button', { name: 'New review' })).toBeDisabled();
  await expect(page.getByText('Deterministic test review.')).toBeVisible();
  await expect(
    page.getByText('Simulated result · no Qwen inference'),
  ).toBeVisible();
  await expect(
    page.getByText('Revision fixture-v1', { exact: false }),
  ).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: testInfo.outputPath('workspace-desktop.png'),
    fullPage: true,
  });
  await page.reload();
  await page.getByRole('button', { name: /python review/ }).click();
  await expect(page.getByText('Deterministic test review.')).toBeVisible();
  await page.getByRole('button', { name: 'Log out' }).click();
  await page.getByLabel('Username').fill(name);
  await page
    .getByLabel('Password', { exact: true })
    .fill('correct-horse-battery');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(
    page.getByRole('button', { name: /python review/ }),
  ).toBeVisible();
});

test('mobile workspace accepts an unsupported language without overflow', async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Create an account' }).click();
  await page.getByLabel('Username').fill(`mobile-${Date.now()}`);
  await page
    .getByLabel('Password', { exact: true })
    .fill('correct-horse-battery');
  await page
    .getByRole('button', { name: 'Create account', exact: true })
    .click();
  await page.getByLabel('Programming language').selectOption('plain');
  await page
    .getByRole('textbox', { name: 'Source code' })
    .fill('IDENTIFICATION DIVISION.\nPROGRAM-ID. DEMO.');
  await page.getByRole('button', { name: 'Run review' }).click();
  await expect(page.getByText('Deterministic test review.')).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('workspace-mobile.png'),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

for (const viewport of [
  { width: 1440, height: 1000 },
  { width: 390, height: 844 },
]) {
  test(`runtime loading, disconnection and recovery at ${viewport.width}px (status fixture)`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    // Only runtime display is intercepted. Accounts/history still use the isolated fake harness.
    let disconnected = false;
    let ready = false;
    let posts = 0;
    page.on('request', (request) => {
      if (
        request.method() === 'POST' &&
        request.url().endsWith('/api/v1/reviews')
      )
        posts++;
    });
    await page.route('**/api/v1/runtime', async (route) => {
      if (disconnected) return route.abort('connectionfailed');
      await route.fulfill({
        json: {
          deployment_environment: 'minikube',
          inference_mode: 'real',
          service_status: ready ? 'ready' : 'model_loading',
          accepting_submissions: ready,
          model_id: 'Qwen/' + 'long-model-display-fixture-'.repeat(6),
          model_revision: '70d244cc86ccca08cf5af4e1e306ecf908b1ad5e',
          model_source: 'backend_configuration',
          device: 'cpu',
        },
      });
    });
    await page.goto('/');
    await page.getByRole('button', { name: 'Create an account' }).click();
    await page
      .getByLabel('Username')
      .fill(`runtime-${viewport.width}-${Date.now()}`);
    await page
      .getByLabel('Password', { exact: true })
      .fill('correct-horse-battery');
    await page
      .getByRole('button', { name: 'Create account', exact: true })
      .click();
    const editor = page.getByRole('textbox', { name: 'Source code' });
    await editor.fill('const preserved = "my unsaved input";');
    const run = page.getByRole('button', { name: 'Run review' });
    const service = page.getByRole('status', { name: 'Service status' });
    await expect(service).toContainText('Model loading');
    await expect(run).toBeDisabled();
    await expect(
      page.getByRole('status', { name: 'Review task status' }),
    ).toContainText('No active review');
    await page.screenshot({
      path: testInfo.outputPath('runtime-loading.png'),
      fullPage: true,
    });
    disconnected = true;
    await expect(service).toContainText('Connection interrupted', {
      timeout: 7000,
    });
    await expect(editor).toHaveText('const preserved = "my unsaved input";');
    await expect(run).toBeDisabled();
    await page.screenshot({
      path: testInfo.outputPath('runtime-disconnected.png'),
      fullPage: true,
    });
    disconnected = false;
    ready = true;
    await expect(run).toBeEnabled({ timeout: 7000 });
    await expect(editor).toHaveText('const preserved = "my unsaved input";');
    expect(posts).toBe(0);
    const about = page.locator('summary', { hasText: 'About this instance' });
    await about.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('details')).toHaveAttribute('open', '');
    await expect(page.getByText(/pinned weight revision/)).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath('runtime-recovered-about.png'),
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}
