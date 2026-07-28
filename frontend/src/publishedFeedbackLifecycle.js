const INVALID_LIFECYCLE_NOTICE = {
  mode: 'error',
  message: 'The review response could not be verified. Please refresh before trying again.'
};

function attemptSignature({ command, symbolIds, comment }) {
  return JSON.stringify({
    command,
    symbolIds: [...symbolIds].sort(),
    comment: comment.trim()
  });
}

export function buildPublishedFeedbackRequest({ command, symbolIds, comment, requestId }) {
  return {
    payload: {
      command,
      symbolIds,
      comment,
      requestId
    }
  };
}

export function createPublishedFeedbackAttempt(current, input, randomUUID = () => crypto.randomUUID()) {
  const signature = attemptSignature(input);
  return current?.signature === signature
    ? current
    : { signature, requestId: randomUUID() };
}

function validString(value) {
  return typeof value === 'string' && value.length > 0;
}

export function publishedFeedbackLifecycleNotice(command, result) {
  const pending = result?.status === 'accepted_pending_delivery';
  const completed = result?.status === 'completed';
  if (
    (!pending && !completed)
    || result?.command !== command
    || result?.publishedAvailabilityChanged !== false
    || !Array.isArray(result?.items)
    || result.items.length === 0
  ) {
    return INVALID_LIFECYCLE_NOTICE;
  }

  const validItems = result.items.every((item) => {
    if (
      !validString(item?.symbolId)
      || !validString(item?.commentId)
      || item?.remainsPublished !== true
      || typeof item?.requestReplayed !== 'boolean'
    ) {
      return false;
    }
    if (command === 'comment') {
      return item.reviewCaseId === null
        && item.edQueueItemId === null
        && item.workflowDeliveryState === 'not_applicable';
    }
    return validString(item.reviewCaseId)
      && validString(item.edQueueItemId)
      && ['materialized', 'pending', 'historical'].includes(item.workflowDeliveryState);
  });
  const deliveryStatesMatch = command === 'comment'
    ? completed
    : pending
      ? result.items.some((item) => item.workflowDeliveryState === 'pending')
      : result.items.every((item) => ['materialized', 'historical'].includes(item.workflowDeliveryState));
  if (!validItems || !deliveryStatesMatch) {
    return INVALID_LIFECYCLE_NOTICE;
  }

  if (command === 'comment') {
    return { mode: 'success', message: 'Comment recorded; the published symbol remains available.' };
  }
  return pending
    ? { mode: 'info', message: 'Review recorded; Ed delivery is pending. The published symbol remains available.' }
    : { mode: 'success', message: 'Review requested; the published symbol remains available.' };
}