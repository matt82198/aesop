/**
 * ErrorBoundary — React error boundary for graceful error handling.
 * Catches rendering errors and displays a user-friendly error message
 * with optional retry action.
 */

import React, { ReactNode } from 'react';
import './ErrorBoundary.css';

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Label for error message (e.g., "Cost data" for the Cost view) */
  label?: string;
  /** Optional callback when retry button is clicked */
  onRetry?: () => void;
  /** CSS class name for the error container */
  className?: string;
  /** Test ID override */
  testId?: string;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error) {
    console.error('ErrorBoundary caught error:', error);
  }

  render() {
    if (this.state.hasError) {
      const label = this.props.label || 'data';
      const testId = this.props.testId || 'error-boundary';

      return (
        <div
          className={`error-boundary ${this.props.className || ''}`}
          role="alert"
          data-testid={testId}
        >
          <div className="error-boundary__content">
            <h3 className="error-boundary__title">Could not load {label}</h3>
            <p className="error-boundary__message">
              {this.state.error?.message || 'An unexpected error occurred.'}
            </p>
            {this.props.onRetry && (
              <button
                type="button"
                className="error-boundary__retry"
                onClick={this.props.onRetry}
              >
                Retry
              </button>
            )}
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
