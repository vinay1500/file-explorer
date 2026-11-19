import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { AuthPanel } from "./AuthPanel";
import { MIN_PASSWORD_LENGTH } from "./credentials";

/**
 * The front door, and the one claim it must never make.
 *
 * `POST /auth/register` answers **202 whether or not the address was free**,
 * with an identical body, so that the endpoint cannot be used to ask who has
 * an account. A form that printed "account created" after a 202 would be
 * stating something the server deliberately refused to tell it -- and would be
 * wrong exactly when the address was already taken, which is the case a
 * visitor most needs the truth about.
 */

const session = vi.hoisted(() => ({
  signInAsGuest: vi.fn(),
  signIn: vi.fn(),
  register: vi.fn(),
}));

vi.mock("../api/session", () => session);

beforeEach(() => {
  session.signInAsGuest.mockReset().mockResolvedValue({ csrf_token: "t" });
  session.signIn.mockReset().mockResolvedValue({ csrf_token: "t" });
  session.register.mockReset().mockResolvedValue(undefined);
});

afterEach(cleanup);

function panel() {
  const onSession = vi.fn();
  const onFailure = vi.fn();
  render(<AuthPanel onSession={onSession} onFailure={onFailure} />);
  return { onSession, onFailure };
}

function form() {
  return screen.getByRole("form", { name: "Account details" });
}

async function openForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /sign in with an email/i }));
}

describe("AuthPanel", () => {
  it("makes the guest session the primary action", () => {
    panel();
    // D-24 is not reversed by M8: the one-click path stays the front door,
    // because an interviewer with thirty seconds will not fill in a form.
    expect(screen.getByRole("button", { name: /continue as guest/i })).toBeInTheDocument();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
  });

  it("signs in with an email and password", async () => {
    const user = userEvent.setup();
    const { onSession } = panel();
    await openForm(user);

    await user.type(screen.getByLabelText("Email"), "person@example.com");
    await user.type(screen.getByLabelText("Password"), "a sufficiently long one");
    await user.click(within(form()).getByRole("button", { name: /^sign in$/i }));

    await waitFor(() => {
      expect(session.signIn).toHaveBeenCalledWith("person@example.com", "a sufficiently long one");
    });
    expect(onSession).toHaveBeenCalled();
  });

  it("registers and then signs in, and never claims the account was created", async () => {
    const user = userEvent.setup();
    // The address was already somebody else's: register still answers 202,
    // and the sign-in that follows is what says so.
    session.signIn.mockRejectedValue(new ApiError(401, null));

    const { onSession } = panel();
    await openForm(user);
    await user.click(screen.getByRole("button", { name: /create account/i }));
    await user.type(screen.getByLabelText("Email"), "taken@example.com");
    await user.type(screen.getByLabelText("Password"), "a sufficiently long one");
    await user.click(
      within(form()).getByRole("button", {
        name: /create account and sign in/i,
      }),
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/already taken, or the password/i);
    });
    expect(session.register).toHaveBeenCalledOnce();
    expect(onSession).not.toHaveBeenCalled();
    expect(screen.queryByText(/account created/i)).not.toBeInTheDocument();
  });

  it("says the same thing for a wrong password as for no such account", async () => {
    const user = userEvent.setup();
    session.signIn.mockRejectedValue(new ApiError(401, null));
    panel();
    await openForm(user);

    await user.type(screen.getByLabelText("Email"), "person@example.com");
    await user.type(screen.getByLabelText("Password"), "a sufficiently long one");
    await user.click(within(form()).getByRole("button", { name: /^sign in$/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/don't match an account/i);
    });
  });

  it("does not register when the mode is sign-in", async () => {
    // Deleting the `mode === "create"` guard made every sign-in attempt also
    // POST /auth/register, and the suite stayed green: unauthenticated
    // account creation for any address somebody types, and two of the ten
    // attempts a minute spent per press. Gate 3 found it by running the
    // mutation.
    const user = userEvent.setup();
    panel();
    await openForm(user);

    await user.type(screen.getByLabelText("Email"), "person@example.com");
    await user.type(screen.getByLabelText("Password"), "a sufficiently long one");
    await user.click(within(form()).getByRole("button", { name: /^sign in$/i }));

    await waitFor(() => {
      expect(session.signIn).toHaveBeenCalledOnce();
    });
    expect(session.register).not.toHaveBeenCalled();
  });

  it("catches a short password before spending a request on it", async () => {
    const user = userEvent.setup();
    panel();
    await openForm(user);

    await user.type(screen.getByLabelText("Email"), "person@example.com");
    await user.type(screen.getByLabelText("Password"), "short");
    await user.click(within(form()).getByRole("button", { name: /^sign in$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(String(MIN_PASSWORD_LENGTH));
    // The auth endpoints allow ten attempts a minute. Spending one of them to
    // be told what the form already knew is a request the visitor pays for.
    expect(session.signIn).not.toHaveBeenCalled();
  });

  it("explains a 429 instead of printing the status", async () => {
    const user = userEvent.setup();
    session.signIn.mockRejectedValue(new ApiError(429, null));
    panel();
    await openForm(user);

    await user.type(screen.getByLabelText("Email"), "person@example.com");
    await user.type(screen.getByLabelText("Password"), "a sufficiently long one");
    await user.click(within(form()).getByRole("button", { name: /^sign in$/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/a lot of attempts/i);
    });
  });
});
