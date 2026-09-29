import { createElement, useEffect, useRef, useState } from 'react';

import { askEd } from './api.js';
import {
  ED_MODE_LABELS,
  ED_PROMPT_LIMIT,
  ED_SOURCE_LABELS,
  ED_SUGGESTED_QUESTIONS,
  describeEdError,
  formatEdTimestamp,
  normalizeEdResponse,
  shortVersion,
} from './edChat.js';

const h = createElement;
const PROMPT_ID = 'ed-prompt';
const HINT_ID = 'ed-prompt-hint';

function Sources({ citations, knowledgeVersion }) {
  if (!citations.length) return null;
  const version = shortVersion(knowledgeVersion);
  return h(
    'details',
    { className: 'ed-sources' },
    h('summary', null, `Sources (${citations.length})`),
    h(
      'ul',
      { className: 'ed-source-list' },
      citations.map((citation) => h(
        'li',
        { key: citation.reference, className: 'ed-source' },
        h('span', { className: 'ed-source-title' }, citation.title),
        h('span', { className: 'tag-chip' }, ED_SOURCE_LABELS[citation.sourceType]),
        citation.asOf ? h('span', { className: 'ed-source-meta' }, `As of ${formatEdTimestamp(citation.asOf)}`) : null,
        h('span', { className: 'ed-source-meta' }, 'Trace ', h('code', null, citation.reference)),
      )),
    ),
    version && citations.some((citation) => citation.sourceType === 'approved_knowledge')
      ? h('p', { className: 'ed-source-meta' }, 'Knowledge version ', h('code', null, version))
      : null,
  );
}

function Attributions({ attributions }) {
  if (!attributions.length) return null;
  return h(
    'div',
    { className: 'ed-attributions' },
    attributions.map((item) => h(
      'aside',
      { key: `${item.source}-${item.licenseCode}`, className: 'ed-attribution', 'aria-label': 'Data attribution' },
      h('p', null, item.attribution),
      h('p', null, item.clarification),
      item.licenseUrl
        ? h('p', null, h('a', { href: item.licenseUrl, target: '_blank', rel: 'noreferrer' }, `Licence: ${item.licenseCode}`))
        : h('p', null, `Licence: ${item.licenseCode}`),
    )),
  );
}

function EdAnswer({ response, onFollowup, disabled }) {
  const tone = response.status === 'answered' ? 'answered' : response.status === 'refused' ? 'refused' : 'unavailable';
  return h(
    'div',
    { className: `ed-answer ed-answer-${tone}` },
    h(
      'div',
      { className: 'ed-turn-heading' },
      h('span', { className: 'ed-turn-speaker' }, 'Ed'),
      h('span', { className: 'status-pill' }, ED_MODE_LABELS[response.mode]),
    ),
    h('p', { className: 'ed-answer-text' }, response.answer),
    response.warnings.length
      ? h('ul', { className: 'ed-warnings' }, response.warnings.map((warning) => h('li', { key: warning }, warning)))
      : null,
    h(Attributions, { attributions: response.attributions }),
    h(Sources, { citations: response.citations, knowledgeVersion: response.knowledgeVersion }),
    response.suggestedFollowups.length
      ? h(
        'div',
        { className: 'ed-followups', role: 'group', 'aria-label': 'Suggested follow-up questions' },
        response.suggestedFollowups.map((question) => h(
          'button',
          { key: question, type: 'button', className: 'action-button compact', disabled, onClick: () => onFollowup(question) },
          question,
        )),
      )
      : null,
  );
}

function Turn({ turn, onFollowup, disabled }) {
  if (turn.role === 'user') {
    return h(
      'li',
      { className: 'ed-turn ed-turn-user' },
      h('div', { className: 'ed-turn-heading' }, h('span', { className: 'ed-turn-speaker' }, 'You')),
      h('p', { className: 'ed-question-text' }, turn.text),
    );
  }
  if (turn.role === 'error') {
    return h(
      'li',
      { className: 'ed-turn ed-turn-error' },
      h('div', { className: 'ed-turn-heading' }, h('span', { className: 'ed-turn-speaker' }, 'Ed')),
      h('p', { className: 'form-message error' }, turn.text),
    );
  }
  return h('li', { className: 'ed-turn ed-turn-ed' }, h(EdAnswer, { response: turn.response, onFollowup, disabled }));
}

export function EdChatPage({ auth }) {
  const [prompt, setPrompt] = useState('');
  const [turns, setTurns] = useState([]);
  const [pending, setPending] = useState(false);
  const [context, setContext] = useState(null);
  const controllerRef = useRef(null);
  const promptRef = useRef(null);
  const nextId = useRef(1);

  // Leaving the page abandons any question in flight.
  useEffect(() => () => controllerRef.current?.abort(), []);

  const organization = context?.organization || auth?.user?.organization?.displayName || null;
  const trimmed = prompt.trim();

  function append(turn) {
    const id = nextId.current;
    nextId.current += 1;
    setTurns((current) => [...current, { id, ...turn }]);
  }

  async function ask(question) {
    const text = question.trim();
    if (!text || pending || text.length > ED_PROMPT_LIMIT) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    append({ role: 'user', text });
    setPrompt('');
    setPending(true);
    const result = await askEd(text, controller.signal);
    if (controller.signal.aborted) {
      append({ role: 'error', text: 'Question cancelled.' });
    } else if (result.ok) {
      const response = normalizeEdResponse(result.payload);
      setContext(response.context);
      append({ role: 'ed', response });
    } else {
      append({ role: 'error', text: describeEdError(result) });
    }
    controllerRef.current = null;
    setPending(false);
    promptRef.current?.focus();
  }

  function clearConversation() {
    setTurns([]);
    setContext(null);
    promptRef.current?.focus();
  }

  function chooseSuggestion(question) {
    setPrompt(question);
    promptRef.current?.focus();
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent?.isComposing) {
      event.preventDefault();
      ask(prompt);
    }
  }

  return h(
    'section',
    { className: 'experience-shell ed-page' },
    h(
      'div',
      { className: 'hero-panel glass-panel standards-hero page-title-row' },
      h(
        'div',
        null,
        h('p', { className: 'eyebrow' }, 'Ed'),
        h('h2', null, 'Ask Ed about Symgov'),
        h(
          'p',
          { className: 'title-support' },
          'Ed explains how Symgov works and answers questions about the records you are allowed to see. It is read-only and cannot change anything.',
        ),
      ),
      h(
        'div',
        { className: 'ed-hero-meta' },
        h('span', { className: 'status-pill' }, 'Read-only'),
        organization
          ? h(
            'p',
            { className: 'ed-context' },
            `Answering for ${organization}${context?.project ? ` · ${context.project}` : ''}`,
          )
          : null,
      ),
    ),
    h(
      'div',
      { className: 'glass-panel pane ed-conversation' },
      h(
        'div',
        { className: 'section-heading' },
        h('h3', null, 'Conversation'),
        h('p', null, 'Kept in this tab only. Copy anything you want to keep.'),
      ),
      turns.length === 0 && !pending
        ? h(
          'div',
          { className: 'ed-empty' },
          h('p', null, 'Ask a question, or start with one of these:'),
          h(
            'ul',
            { className: 'ed-suggestions', 'aria-label': 'Suggested questions' },
            ED_SUGGESTED_QUESTIONS.map((question) => h(
              'li',
              { key: question },
              h('button', { type: 'button', className: 'action-button compact', onClick: () => chooseSuggestion(question) }, question),
            )),
          ),
        )
        : null,
      h(
        'ol',
        { className: 'ed-transcript', role: 'log', 'aria-live': 'polite', 'aria-label': 'Conversation with Ed', 'aria-busy': pending },
        turns.map((turn) => h(Turn, { key: turn.id, turn, onFollowup: ask, disabled: pending })),
        pending
          ? h('li', { className: 'ed-turn ed-turn-pending' }, h('p', null, 'Ed is working on an answer…'))
          : null,
      ),
    ),
    h(
      'form',
      {
        className: 'glass-panel pane ed-composer',
        onSubmit: (event) => {
          event.preventDefault();
          ask(prompt);
        },
      },
      h(
        'label',
        { className: 'field', htmlFor: PROMPT_ID },
        h('span', null, 'Your question'),
        h('textarea', {
          id: PROMPT_ID,
          ref: promptRef,
          rows: 3,
          maxLength: ED_PROMPT_LIMIT,
          value: prompt,
          'aria-describedby': HINT_ID,
          onChange: (event) => setPrompt(event.target.value),
          onKeyDown: handleKeyDown,
          placeholder: 'For example: how do I choose an organization when I sign in?',
        }),
      ),
      h(
        'div',
        { className: 'ed-composer-row' },
        h(
          'p',
          { id: HINT_ID, className: 'ed-hint' },
          `Enter to send, Shift+Enter for a new line. ${prompt.length.toLocaleString('en-GB')} / ${ED_PROMPT_LIMIT.toLocaleString('en-GB')}`,
        ),
        h(
          'div',
          { className: 'ed-composer-actions' },
          pending
            ? h('button', { type: 'button', className: 'action-button ghost', onClick: () => controllerRef.current?.abort() }, 'Cancel')
            : h(
              'button',
              { type: 'button', className: 'action-button ghost', disabled: turns.length === 0, onClick: clearConversation },
              'Clear conversation',
            ),
          h('button', { type: 'submit', className: 'action-button primary', disabled: pending || !trimmed }, pending ? 'Asking…' : 'Ask Ed'),
        ),
      ),
    ),
  );
}
