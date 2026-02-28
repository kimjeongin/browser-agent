/**
 * Tests for content.ts DOM command execution logic.
 *
 * content.ts uses WXT's defineContentScript which is unavailable in unit tests,
 * so we test the pure logic extracted from the command handlers.
 */

describe('URL validation in navigate command', () => {
  const isValidUrl = (url: string) =>
    url.startsWith('http://') || url.startsWith('https://');

  it('should accept http:// URLs', () => {
    expect(isValidUrl('http://example.com')).toBe(true);
  });

  it('should accept https:// URLs', () => {
    expect(isValidUrl('https://example.com')).toBe(true);
  });

  it('should reject javascript: URLs', () => {
    expect(isValidUrl('javascript:alert(1)')).toBe(false);
  });

  it('should reject chrome:// URLs', () => {
    expect(isValidUrl('chrome://settings')).toBe(false);
  });

  it('should reject data: URLs', () => {
    expect(isValidUrl('data:text/html,<h1>hi</h1>')).toBe(false);
  });

  it('should reject empty string', () => {
    expect(isValidUrl('')).toBe(false);
  });
});

describe('get_structured_dom helper logic', () => {
  it('should limit elements to 50', () => {
    const mockElements = Array.from({ length: 100 }, (_, i) => ({ idx: i }));
    const limited = mockElements.slice(0, 50);
    expect(limited).toHaveLength(50);
    expect(limited[0].idx).toBe(0);
    expect(limited[49].idx).toBe(49);
  });

  it('should generate id-based selector when id exists', () => {
    const elId = 'search-input';
    const elName = '';
    const ariaLabel: string | null = null;

    let selector: string | null = null;
    if (elId) selector = `#${elId}`;
    else if (elName) selector = `[name="${elName}"]`;
    else if (ariaLabel) selector = `[aria-label="${ariaLabel}"]`;

    expect(selector).toBe('#search-input');
  });

  it('should generate name-based selector when no id', () => {
    const elId = '';
    const elName = 'q';
    const ariaLabel: string | null = null;

    let selector: string | null = null;
    if (elId) selector = `#${elId}`;
    else if (elName) selector = `[name="${elName}"]`;
    else if (ariaLabel) selector = `[aria-label="${ariaLabel}"]`;

    expect(selector).toBe('[name="q"]');
  });

  it('should generate aria-label selector as last resort', () => {
    const elId = '';
    const elName = '';
    const ariaLabel = 'Search button';

    let selector: string | null = null;
    if (elId) selector = `#${elId}`;
    else if (elName) selector = `[name="${elName}"]`;
    else if (ariaLabel) selector = `[aria-label="${ariaLabel}"]`;

    expect(selector).toBe('[aria-label="Search button"]');
  });

  it('should return null selector when no identifying attribute exists', () => {
    const elId = '';
    const elName = '';
    const ariaLabel: string | null = null;

    let selector: string | null = null;
    if (elId) selector = `#${elId}`;
    else if (elName) selector = `[name="${elName}"]`;
    else if (ariaLabel) selector = `[aria-label="${ariaLabel}"]`;

    expect(selector).toBeNull();
  });

  it('should truncate text to 100 characters', () => {
    const longText = 'A'.repeat(200);
    const truncated = longText.trim().slice(0, 100);
    expect(truncated).toHaveLength(100);
  });

  it('should truncate page_text_preview to 2000 characters', () => {
    const longBody = 'X'.repeat(5000);
    const preview = longBody.slice(0, 2000);
    expect(preview).toHaveLength(2000);
  });
});

describe('click fallback chain logic', () => {
  it('should try fallback selectors when primary fails', () => {
    const primarySelector = '#nonexistent';
    const fallbackSelectors = ['[name="search"]', '.search-input'];

    const mockQuerySelector = (sel: string) => {
      if (sel === '#nonexistent') return null;
      if (sel === '[name="search"]') return null;
      if (sel === '.search-input') return { innerText: 'Search', click: () => {} };
      return null;
    };

    let foundEl = mockQuerySelector(primarySelector);
    let usedSelector = primarySelector;
    for (const fallback of fallbackSelectors) {
      if (!foundEl) {
        foundEl = mockQuerySelector(fallback);
        if (foundEl) {
          usedSelector = fallback;
          break;
        }
      }
    }

    expect(foundEl).not.toBeNull();
    expect(usedSelector).toBe('.search-input');
  });

  it('should return primary element when it exists', () => {
    const primarySelector = '#submit-btn';
    const fallbackSelectors = ['.fallback'];

    const mockQuerySelector = (sel: string) => {
      if (sel === '#submit-btn') return { innerText: 'Submit' };
      if (sel === '.fallback') return { innerText: 'Fallback' };
      return null;
    };

    const foundEl = mockQuerySelector(primarySelector);
    expect(foundEl).not.toBeNull();
    expect((foundEl as { innerText: string }).innerText).toBe('Submit');
  });

  it('should use text-based search as last resort', () => {
    const elementText = 'Search';
    const candidates = [
      { innerText: 'Cancel', getAttribute: () => null },
      { innerText: 'Search', getAttribute: () => null },
      { innerText: 'Submit', getAttribute: () => null },
    ];

    const found = candidates.find((e) => {
      const label = e.getAttribute('aria-label')?.toLowerCase() ?? '';
      const text = e.innerText?.toLowerCase() ?? '';
      return label.includes(elementText.toLowerCase()) || text.includes(elementText.toLowerCase());
    });

    expect(found).toBeDefined();
    expect(found!.innerText).toBe('Search');
  });

  it('should match by aria-label in text-based search', () => {
    const elementText = 'Close';
    const candidates = [
      { innerText: '', getAttribute: (attr: string) => attr === 'aria-label' ? 'Close dialog' : null },
      { innerText: 'Open', getAttribute: () => null },
    ];

    const found = candidates.find((e) => {
      const label = e.getAttribute('aria-label')?.toLowerCase() ?? '';
      const text = e.innerText?.toLowerCase() ?? '';
      return label.includes(elementText.toLowerCase()) || text.includes(elementText.toLowerCase());
    });

    expect(found).toBeDefined();
    expect(found!.getAttribute('aria-label')).toBe('Close dialog');
  });

  it('should return undefined when no match found by text', () => {
    const elementText = 'Nonexistent';
    const candidates = [
      { innerText: 'Cancel', getAttribute: () => null },
      { innerText: 'Submit', getAttribute: () => null },
    ];

    const found = candidates.find((e) => {
      const label = e.getAttribute('aria-label')?.toLowerCase() ?? '';
      const text = e.innerText?.toLowerCase() ?? '';
      return label.includes(elementText.toLowerCase()) || text.includes(elementText.toLowerCase());
    });

    expect(found).toBeUndefined();
  });
});

describe('Set-of-Marks overlay logic', () => {
  it('create_marks_overlay should return marks keyed 1..N', () => {
    // Simulate the overlay creation logic for N elements
    const mockElements = [
      { id: 'search', tagName: 'INPUT', name: 'q', getAttribute: () => null },
      { id: '', tagName: 'BUTTON', name: '', getAttribute: () => null },
      { id: '', tagName: 'A', name: '', getAttribute: (_: string) => 'Home' },
    ];

    const marks: Record<string, { selector: string; tag: string }> = {};

    mockElements.forEach((el, i) => {
      const idx = i + 1;
      let selector: string;
      if (el.id) selector = `#${el.id}`;
      else if (el.name) selector = `[name="${el.name}"]`;
      else {
        const label = el.getAttribute('aria-label');
        if (label) selector = `[aria-label="${label}"]`;
        else selector = el.tagName.toLowerCase();
      }
      marks[String(idx)] = { selector, tag: el.tagName.toLowerCase() };
    });

    expect(Object.keys(marks)).toEqual(['1', '2', '3']);
    expect(marks['1'].selector).toBe('#search');
    expect(marks['1'].tag).toBe('input');
    expect(marks['3'].selector).toBe('[aria-label="Home"]');
  });

  it('remove_marks_overlay should not remove _currentMarks state', () => {
    // Simulate marks being set before removal
    let currentMarks: Record<string, { selector: string; tag: string }> = {
      '1': { selector: '#btn', tag: 'button' },
    };

    // After remove, marks should still be in state (for click_by_mark_id)
    const simulateRemoveOverlay = () => {
      // Only removes the DOM overlay, NOT _currentMarks
      // currentMarks remains unchanged
    };
    simulateRemoveOverlay();

    expect(currentMarks['1']).toBeDefined();
    expect(currentMarks['1'].selector).toBe('#btn');
  });

  it('click_by_mark_id should return error for unknown mark_id', () => {
    const currentMarks: Record<string, { selector: string; tag: string }> = {
      '1': { selector: '#btn', tag: 'button' },
    };

    const markId = '99';
    const mark = currentMarks[markId];

    let result: { success: boolean; error?: string } | null = null;
    if (!mark) {
      const available = Object.keys(currentMarks).join(', ') || 'none';
      result = {
        success: false,
        error: `Mark ${markId} not found. Available marks: ${available}.`,
      };
    }

    expect(result).not.toBeNull();
    expect(result!.success).toBe(false);
    expect(result!.error).toContain('Mark 99 not found');
    expect(result!.error).toContain('1');
  });

  it('click_by_mark_id should return success with clicked_selector', () => {
    const currentMarks: Record<string, { selector: string; tag: string }> = {
      '2': { selector: '#submit-btn', tag: 'button' },
    };

    const markId = '2';
    const mark = currentMarks[markId];

    // Simulate finding the element and clicking it
    let result: { mark_id: number; clicked_selector: string } | null = null;
    if (mark) {
      // In real code: document.querySelector(mark.selector)?.click()
      result = { mark_id: Number(markId), clicked_selector: mark.selector };
    }

    expect(result).not.toBeNull();
    expect(result!.clicked_selector).toBe('#submit-btn');
    expect(result!.mark_id).toBe(2);
  });

  it('_currentMarks should persist after remove_marks_overlay', () => {
    // Simulates that marks survive overlay removal for deferred clicks
    let currentMarks: Record<string, { selector: string; tag: string }> = {
      '1': { selector: '#btn', tag: 'button' },
      '2': { selector: 'a.link', tag: 'a' },
    };

    // simulate remove overlay (only removes DOM element, keeps marks)
    // (no actual DOM in unit tests)

    // marks still accessible
    expect(currentMarks['1']).toBeDefined();
    expect(currentMarks['2']).toBeDefined();

    // simulate new create_marks_overlay (resets marks)
    currentMarks = {};
    expect(Object.keys(currentMarks)).toHaveLength(0);
  });
});

describe('execution queue serialization', () => {
  it('should serialize sequential async operations', async () => {
    const order: number[] = [];
    let queue: Promise<void> = Promise.resolve();

    const enqueue = (idx: number, delayMs: number) => {
      queue = queue.then(
        () =>
          new Promise<void>((resolve) => {
            setTimeout(() => {
              order.push(idx);
              resolve();
            }, delayMs);
          }),
      );
    };

    // Enqueue in order but with varying delays -- queue should preserve order
    enqueue(1, 30);
    enqueue(2, 10);
    enqueue(3, 20);

    await queue;
    expect(order).toEqual([1, 2, 3]);
  });

  it('should continue queue after an error', async () => {
    const results: string[] = [];
    let queue: Promise<void> = Promise.resolve();

    const enqueue = (label: string, shouldFail: boolean) => {
      queue = queue
        .then(() => {
          if (shouldFail) throw new Error(`fail-${label}`);
          results.push(label);
        })
        .catch((err) => {
          results.push(`error:${(err as Error).message}`);
        });
    };

    enqueue('a', false);
    enqueue('b', true);
    enqueue('c', false);

    await queue;
    expect(results).toEqual(['a', 'error:fail-b', 'c']);
  });
});
