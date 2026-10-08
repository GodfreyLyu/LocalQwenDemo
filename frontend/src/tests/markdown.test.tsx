import '../test-support/mock-editor';
import { render, screen } from '@testing-library/react';
import { Markdown } from '../Markdown';

it('removes raw HTML, unsafe links, and remotely loaded images from model output', () => {
  const { container } = render(
    <Markdown
      content={
        '# Review\n<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n\n[bad](javascript:alert%281%29)\n![tracking](https://tracker.example/pixel)\n**Safe content**'
      }
    />,
  );
  expect(container.querySelector('script, img, iframe')).toBeNull();
  expect(container.innerHTML).not.toContain('href="javascript:');
  expect(screen.getByText('Safe content')).toBeInTheDocument();
});
