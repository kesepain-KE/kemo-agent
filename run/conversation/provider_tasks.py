"""Child-result safe point after a provider tries to finish its response."""
from events import RunEvent


def prepare_shell_request(inbox, *, last_iteration, messages, cancel_event, commit_cancelled_round):
    """Use the last model opportunity to consume required Shell completions."""
    if last_iteration and inbox.pending():
        yield RunEvent(type="background_job_progress", metadata={
            "phase": "shell", "status": "waiting_shell", "root_task_id": inbox.root_task_id,
        })
        if not inbox.wait(cancel_event):
            yield commit_cancelled_round()
            return False
    notification = inbox.take()
    if notification:
        messages.append(notification)
    return True


def settle_shell(inbox, *, messages, iteration_text, cancel_event, commit_cancelled_round):
    if inbox.pending():
        yield RunEvent(type="background_job_progress", metadata={
            "phase": "shell", "status": "waiting_shell", "root_task_id": inbox.root_task_id,
        })
        if not inbox.wait(cancel_event):
            yield commit_cancelled_round()
            return "cancelled"
    notification = inbox.take()
    if notification:
        messages.append({"role": "assistant", "content": "".join(iteration_text)})
        messages.append(notification)
        return "continue"
    return "finish"


def settle_children(coordinator, *, messages, iteration_text, all_text, observed_text,
                    cancel_event, guidance_channel, drain_guidance, prepare_guidance,
                    commit_cancelled_round):
    waiting = bool(coordinator.pending())
    if waiting:
        messages.append({"role": "assistant", "content": "".join(iteration_text)})
        yield RunEvent(type="subagent_progress", metadata={
            "phase": "subagent", "status": "waiting_children", "root_task_id": coordinator.id,
        })
        if not coordinator.wait(cancel_event, wake=lambda: (
            guidance_channel is not None and callable(getattr(guidance_channel, "qsize", None))
            and guidance_channel.qsize() > 0
        )):
            yield commit_cancelled_round()
            return "cancelled"
    # Also handles a child which finished during the just-accepted provider IO.
    notification = coordinator.notifications()
    guidance = drain_guidance(guidance_channel) if waiting or notification else []
    if notification or guidance:
        if not waiting:
            messages.append({"role": "assistant", "content": "".join(iteration_text)})
        if notification:
            messages.append(notification)
        messages.extend(prepare_guidance(guidance))
        all_text.append("\n\n")
        observed_text.append("\n\n")
        yield RunEvent(type="text_delta", content="\n\n")
        return "continue"
    return "finish"
