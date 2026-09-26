import { Component, createElement } from 'react';

// Contains a render failure to one panel, so the rest of the page stays up.
// Unmounting the boundary (e.g. collapsing the panel) clears the failure.
export default class PanelErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error, info) {
    console.error(`${this.props.label || 'Panel'} failed to render.`, error, info?.componentStack);
  }

  render() {
    if (this.state.failed) {
      return createElement(
        'p',
        { className: 'inline-status error', role: 'alert' },
        `${this.props.label || 'This panel'} could not be shown. The rest of the page still works.`
      );
    }
    return this.props.children;
  }
}
