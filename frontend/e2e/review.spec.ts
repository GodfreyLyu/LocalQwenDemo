import { test, expect } from '@playwright/test';

test('register, review, refresh persisted history, logout, and login', async ({
  page,
}, testInfo) => {
  const name = `reviewer-${Date.now()}`;
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await expect(
    page.getByRole('status', { name: 'Service status' }),
  ).toContainText('Ready');
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
    page.getByText('Simulated model (no Qwen inference)', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('status', { name: 'Service status' }),
  ).toContainText('Ready');
  await expect(
    page.getByText('Submit code to see the review here.'),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('workspace-empty-desktop.png'),
    fullPage: true,
  });
  const runBounds = await page
    .getByRole('button', { name: 'Run review' })
    .boundingBox();
  expect(runBounds!.y + runBounds!.height).toBeLessThanOrEqual(
    page.viewportSize()!.height,
  );
  await page.getByLabel('Programming language').focus();
  await page.keyboard.press('Tab');
  await expect(
    page.getByRole('textbox', { name: 'Source code' }),
  ).toBeFocused();
  // Preserve CodeMirror's indentation binding; Escape then Tab moves focus out.
  await page.keyboard.press('Escape');
  await page.keyboard.press('Tab');
  await expect(
    page.getByRole('button', { name: 'Load example' }),
  ).toBeFocused();
  await page.keyboard.press('Enter');
  await page.getByRole('button', { name: 'Run review' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'New review' })).toBeDisabled();
  await expect(page.getByText('Deterministic test review.')).toBeVisible();
  await expect(
    page.getByText('Simulated result · no Qwen inference'),
  ).toBeVisible();
  await page.locator('.result-provenance summary').click();
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
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await expect(
    page.getByRole('status', { name: 'Service status' }),
  ).toContainText('Ready');
  await page.screenshot({
    path: testInfo.outputPath('sign-in-mobile.png'),
    fullPage: true,
  });
  await page.getByRole('button', { name: 'Create an account' }).click();
  await page.getByLabel('Username').fill(`mobile-${Date.now()}`);
  await page
    .getByLabel('Password', { exact: true })
    .fill('correct-horse-battery');
  await page
    .getByRole('button', { name: 'Create account', exact: true })
    .click();
  await expect(
    page.getByText('Submit code to see the review here.'),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('workspace-empty-mobile.png'),
    fullPage: true,
  });
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
      .fill(`runtime-${viewport.width}-${Date.now()}`.padEnd(64, 'x'));
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
    await editor.fill('x'.repeat(1000));
    await expect(editor).toHaveText('x'.repeat(1000));
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

for (const width of [1280, 390]) {
  test(`compact account forms preserve keyboard flow and errors at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 800 });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
    const username = page.getByLabel('Username');
    const password = page.getByLabel('Password', { exact: true });
    await username.focus();
    await expect(username).toHaveCSS('outline-style', 'solid');
    await page.keyboard.type(`missing-${Date.now()}`);
    await page.keyboard.press('Tab');
    await expect(password).toBeFocused();
    await page.keyboard.type('correct-horse-battery');
    await page.keyboard.press('Tab');
    await expect(
      page.getByRole('button', { name: 'Sign in', exact: true }),
    ).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('alert')).toContainText(
      'Incorrect login identifier or password.',
    );
    await page.screenshot({
      path: testInfo.outputPath(`sign-in-error-${width}.png`),
      fullPage: true,
    });
    const typed = await username.inputValue();
    await page.getByRole('button', { name: 'Create an account' }).click();
    await expect(
      page.getByRole('heading', { name: 'Create account' }),
    ).toBeVisible();
    await expect(password).toHaveAttribute('autocomplete', 'new-password');
    await expect(username).toHaveValue(typed);
    await expect(page.getByRole('alert')).toHaveCount(0);
    await page.screenshot({
      path: testInfo.outputPath(`register-${width}.png`),
      fullPage: true,
    });
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(password).toHaveAttribute('autocomplete', 'current-password');
    const about = page.locator('summary', { hasText: 'About this instance' });
    await page.keyboard.press('Tab');
    await expect(about).toBeFocused();
    await expect(about).toHaveCSS('outline-style', 'solid');
    await page.keyboard.press('Enter');
    await expect(page.locator('.about-instance')).toHaveAttribute('open', '');
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    // Check the form's actual text colors against its white background without adding a library.
    const contrasts = await page.evaluate(() => {
      const luminance = (rgb: string) => {
        const parts = rgb
          .match(/[\d.]+/g)!
          .slice(0, 3)
          .map(Number)
          .map((x) => {
            const channel = x / 255;
            return channel <= 0.04045
              ? channel / 12.92
              : ((channel + 0.055) / 1.055) ** 2.4;
          });
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2];
      };
      return [
        '.auth-description',
        '.auth-footnote',
        '.runtime-line',
        '.simulated-label',
        'label',
      ].map((selector) => {
        const foreground = luminance(
          getComputedStyle(document.querySelector(selector)!).color,
        );
        return 1.05 / (foreground + 0.05);
      });
    });
    expect(contrasts.every((ratio) => ratio >= 4.5)).toBe(true);
  });
}
