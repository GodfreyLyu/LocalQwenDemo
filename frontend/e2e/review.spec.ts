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
  await page.getByRole('button', { name: /Try a small example/ }).click();
  await page.getByRole('button', { name: 'Run Review' }).click();
  await expect(page.getByRole('button', { name: 'New review' })).toBeDisabled();
  await expect(page.getByText('Deterministic test review.')).toBeVisible();
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
  await page.getByRole('button', { name: 'Run Review' }).click();
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
